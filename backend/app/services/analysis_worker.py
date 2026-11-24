"""Persisted in-process queue for AI analysis of completed explorations."""

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload, sessionmaker

from app.core.config import Settings, get_settings
from app.models.exploration import Exploration, ExplorationAnalysis, ExplorationPage
from app.services.ai_provider import AIProviderError
from app.services.analysis_service import AIAnalysisService
from app.services.test_case_service import sync_ai_test_cases

logger = logging.getLogger("software_intelligence.analysis_worker")


class AnalysisManager:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: Settings | None = None,
        analyzer_factory: Callable[[], AIAnalysisService] | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings or get_settings()
        self.analyzer_factory = analyzer_factory or (lambda: AIAnalysisService(self.settings))
        self._semaphore = asyncio.Semaphore(self.settings.ai_analysis_worker_concurrency)
        self._tasks: dict[UUID, asyncio.Task[None]] = {}
        self._stopping = False

    async def start(self) -> None:
        self._stopping = False
        try:
            with self.session_factory() as session:
                interrupted = list(
                    session.scalars(
                        select(ExplorationAnalysis).where(
                            ExplorationAnalysis.status.in_(("queued", "running"))
                        )
                    )
                )
                for analysis in interrupted:
                    if analysis.status == "running":
                        analysis.status = "queued"
                        analysis.started_at = None
                        analysis.error_message = None
                exploration_ids = [item.exploration_id for item in interrupted]
                session.commit()
        except Exception:
            logger.exception("analysis_queue_recovery_failed")
            return
        for exploration_id in exploration_ids:
            self.enqueue(exploration_id)

    def enqueue(self, exploration_id: UUID) -> None:
        if self._stopping:
            return
        task = self._tasks.get(exploration_id)
        if task is not None and not task.done():
            return
        self._tasks[exploration_id] = asyncio.create_task(
            self._run(exploration_id), name=f"analysis-{exploration_id}"
        )

    async def stop(self) -> None:
        self._stopping = True
        tasks = list(self._tasks.values())
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _run(self, exploration_id: UUID) -> None:
        try:
            async with self._semaphore:
                await self._execute(exploration_id)
        except asyncio.CancelledError:
            self._return_to_queue(exploration_id)
            raise
        except Exception:
            logger.exception(
                "analysis_worker_crashed", extra={"exploration_id": str(exploration_id)}
            )
            self._finish_failed(exploration_id, "AI analysis encountered an unexpected error.")
        finally:
            self._tasks.pop(exploration_id, None)
            if not self._stopping and self._is_queued(exploration_id):
                self.enqueue(exploration_id)

    async def _execute(self, exploration_id: UUID) -> None:
        with self.session_factory() as session:
            analysis = session.scalar(
                select(ExplorationAnalysis).where(
                    ExplorationAnalysis.exploration_id == exploration_id
                )
            )
            exploration = session.get(Exploration, exploration_id)
            if analysis is None or exploration is None or analysis.status != "queued":
                return
            pages = list(
                session.scalars(
                    select(ExplorationPage)
                    .options(selectinload(ExplorationPage.elements))
                    .where(ExplorationPage.exploration_id == exploration_id)
                    .order_by(ExplorationPage.depth, ExplorationPage.discovered_at, ExplorationPage.url)
                )
            )
            if exploration.status != "completed" or not pages:
                self._set_failed(
                    session,
                    analysis,
                    "AI analysis requires a completed exploration with saved pages.",
                )
                return
            start_url = exploration.start_url
            analysis.status = "running"
            analysis.started_at = datetime.now(UTC)
            analysis.completed_at = None
            analysis.error_message = None
            analysis.model = self.settings.ai_model
            session.commit()

        analyzer: AIAnalysisService | None = None
        try:
            analyzer = self.analyzer_factory()
            result = await analyzer.analyze(start_url, pages)
            selected_model = getattr(
                getattr(analyzer, "provider", None), "response_model", None
            )
        except AIProviderError as exc:
            selected_model = getattr(
                getattr(analyzer, "provider", None), "response_model", None
            )
            self._finish_failed(
                exploration_id, exc.public_message, selected_model=selected_model
            )
            return
        except Exception:
            logger.exception(
                "analysis_generation_failed", extra={"exploration_id": str(exploration_id)}
            )
            self._finish_failed(exploration_id, "AI analysis could not be completed.")
            return

        with self.session_factory() as session:
            analysis = session.scalar(
                select(ExplorationAnalysis).where(
                    ExplorationAnalysis.exploration_id == exploration_id
                )
            )
            if analysis is None or analysis.status != "running":
                return
            analysis.result = result.model_dump(mode="json")
            if isinstance(selected_model, str) and selected_model.strip():
                analysis.model = selected_model
            analysis.status = "completed"
            analysis.error_message = None
            analysis.completed_at = datetime.now(UTC)
            pages = list(
                session.scalars(
                    select(ExplorationPage)
                    .options(selectinload(ExplorationPage.elements))
                    .where(ExplorationPage.exploration_id == exploration_id)
                    .order_by(ExplorationPage.depth, ExplorationPage.discovered_at, ExplorationPage.url)
                )
            )
            try:
                sync_ai_test_cases(session, analysis, pages)
                session.commit()
            except Exception:
                # Keep the completed analysis durable. The test-case endpoint
                # retries this deterministic synchronization on the next read.
                session.rollback()
                logger.exception(
                    "test_case_sync_after_analysis_failed",
                    extra={"exploration_id": str(exploration_id)},
                )
                with self.session_factory() as recovery_session:
                    saved_analysis = recovery_session.scalar(
                        select(ExplorationAnalysis).where(
                            ExplorationAnalysis.exploration_id == exploration_id
                        )
                    )
                    if saved_analysis is not None and saved_analysis.status == "running":
                        saved_analysis.result = result.model_dump(mode="json")
                        if isinstance(selected_model, str) and selected_model.strip():
                            saved_analysis.model = selected_model
                        saved_analysis.status = "completed"
                        saved_analysis.error_message = None
                        saved_analysis.completed_at = datetime.now(UTC)
                        recovery_session.commit()

    @staticmethod
    def _set_failed(session: Session, analysis: ExplorationAnalysis, message: str) -> None:
        analysis.status = "failed"
        analysis.error_message = message[:2000]
        analysis.completed_at = datetime.now(UTC)
        session.commit()

    def _finish_failed(
        self, exploration_id: UUID, message: str, *, selected_model: str | None = None
    ) -> None:
        try:
            with self.session_factory() as session:
                analysis = session.scalar(
                    select(ExplorationAnalysis).where(
                        ExplorationAnalysis.exploration_id == exploration_id
                    )
                )
                if analysis is None or analysis.status not in {"queued", "running"}:
                    return
                if isinstance(selected_model, str) and selected_model.strip():
                    analysis.model = selected_model
                self._set_failed(session, analysis, message)
        except Exception:
            logger.exception("analysis_failure_persist_failed")

    def _return_to_queue(self, exploration_id: UUID) -> None:
        try:
            with self.session_factory() as session:
                analysis = session.scalar(
                    select(ExplorationAnalysis).where(
                        ExplorationAnalysis.exploration_id == exploration_id
                    )
                )
                if analysis is not None and analysis.status == "running":
                    analysis.status = "queued"
                    analysis.started_at = None
                    session.commit()
        except Exception:
            logger.exception("analysis_requeue_persist_failed")

    def _is_queued(self, exploration_id: UUID) -> bool:
        try:
            with self.session_factory() as session:
                status = session.scalar(
                    select(ExplorationAnalysis.status).where(
                        ExplorationAnalysis.exploration_id == exploration_id
                    )
                )
                return status == "queued"
        except Exception:
            logger.exception("analysis_queue_state_check_failed")
            return False
