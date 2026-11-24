"""Asynchronous worker for persistent, evidence-bound browser test runs."""

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload, sessionmaker

from app.core.config import Settings, get_settings
from app.models.exploration import Exploration, ExplorationPage
from app.models.interaction_observation import InteractionObservation
from app.models.test_case import TestCase
from app.models.test_execution import TestRun, TestRunStep
from app.schemas.analysis import WorkflowSuggestion
from app.schemas.test_execution import UserWorkflowSuggestion
from app.services.browser_explorer import BrowserExplorationEngine
from app.services.test_planner import (
    CompiledScenario,
    InvalidScenario,
    compile_manual_login_smoke_test,
    compile_scenario,
    compile_user_scenario,
)
from app.services.test_interactions import load_interaction_observations

logger = logging.getLogger("software_intelligence.test_execution_worker")


def _duration_since(started_at: datetime | None, finished_at: datetime) -> int | None:
    if started_at is None:
        return None
    normalized_start = started_at if started_at.tzinfo is not None else started_at.replace(tzinfo=UTC)
    return max(0, int((finished_at - normalized_start).total_seconds() * 1000))


def _compile_saved_run_snapshot(
    run: TestRun,
    pages: list[ExplorationPage],
    exploration_start_url: str,
    observations: dict[UUID, InteractionObservation] | None = None,
) -> CompiledScenario:
    """Validate the immutable run payload without consulting current AI/case data."""
    snapshot = run.scenario
    if not isinstance(snapshot, dict):
        raise InvalidScenario("The saved test run snapshot is invalid.")

    if snapshot.get("schema_version") == 1:
        source = snapshot.get("source")
        execution_kind = snapshot.get("execution_kind")
        scenario = snapshot.get("scenario")
        saved_definition_steps = snapshot.get("steps")
        if run.test_case_id is None or snapshot.get("test_case_id") != str(run.test_case_id):
            raise InvalidScenario("The run snapshot does not match its saved test case ID.")
        if source not in {"ai_generated", "user_created"}:
            raise InvalidScenario("The saved test case source is unsupported.")
        if not isinstance(saved_definition_steps, list):
            raise InvalidScenario("The saved test case execution plan is invalid.")
    else:
        # Runs created before migration 0006 already contain a complete scenario
        # snapshot and compiled step rows. Keep pending legacy runs recoverable.
        source = snapshot.get("source", "ai_generated")
        execution_kind = "manual_login_smoke" if source == "manual" else "workflow"
        scenario = {key: value for key, value in snapshot.items() if key != "scenario_index"}
        saved_definition_steps = None
        if source not in {"manual", "ai_generated"}:
            raise InvalidScenario("The saved legacy run source is unsupported.")

    if not isinstance(scenario, dict):
        raise InvalidScenario("The saved scenario snapshot is invalid.")
    if execution_kind == "manual_login_smoke" and source in {"user_created", "manual"}:
        compiled = compile_manual_login_smoke_test(
            UUID(scenario["page_id"]),
            UUID(scenario["login_button_element_id"]),
            pages,
            exploration_start_url,
        )
    elif execution_kind == "workflow" and source in {"ai_generated", "user_created"}:
        if source == "user_created":
            workflow = UserWorkflowSuggestion.model_validate(scenario)
            compiled = compile_user_scenario(
                workflow,
                run.exploration_id,
                pages,
                observations or {},
            )
        else:
            workflow = WorkflowSuggestion.model_validate(scenario)
            compiled = compile_scenario(0, workflow, pages)
    else:
        raise InvalidScenario("The saved execution kind is unsupported.")

    if scenario != compiled.scenario:
        raise InvalidScenario("The scenario snapshot no longer matches persisted evidence.")

    if saved_definition_steps is not None:
        expected_definition_steps = [
            {
                "ordinal": step.ordinal,
                "instruction": step.instruction,
                "action": step.action,
                "expected_evidence": step.expected_evidence,
            }
            for step in compiled.steps
        ]
        if saved_definition_steps != expected_definition_steps:
            raise InvalidScenario("The saved test case definition does not match its validated scenario.")

    if len(run.steps) != len(compiled.steps) or any(
        saved.ordinal != planned.ordinal
        or saved.instruction != planned.instruction
        or saved.action != planned.action
        or saved.expected_evidence != planned.expected_evidence
        for saved, planned in zip(run.steps, compiled.steps, strict=True)
    ):
        raise InvalidScenario("The saved execution steps do not match the run scenario snapshot.")
    return compiled


class TestExecutionManager:
    __test__ = False
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: Settings | None = None,
        engine_factory: Callable[[], BrowserExplorationEngine] | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings or get_settings()
        self.engine_factory = engine_factory or (lambda: BrowserExplorationEngine(self.settings))
        self._semaphore = asyncio.Semaphore(1)
        self._tasks: dict[UUID, asyncio.Task[None]] = {}
        self._cancel_events: dict[UUID, asyncio.Event] = {}
        self._stopping = False

    async def start(self) -> None:
        self._stopping = False
        pending_ids: list[UUID] = []
        try:
            with self.session_factory() as session:
                interrupted = list(
                    session.scalars(
                        select(TestRun)
                        .options(selectinload(TestRun.steps))
                        .where(TestRun.status.in_(('pending', 'running')))
                    )
                )
                for run in interrupted:
                    if run.status == "running":
                        now = datetime.now(UTC)
                        run.status = "failed"
                        run.error_message = "The browser worker stopped during this run; it was not replayed."
                        run.completed_at = now
                        for step in run.steps:
                            if step.status == "running":
                                step.status = "failed"
                                step.duration_ms = _duration_since(step.started_at, now)
                                step.error_message = "The browser worker stopped before this step result was saved."
                                step.completed_at = now
                            elif step.status == "pending":
                                step.status = "cancelled"
                                step.error_message = "Not run because the worker stopped during an earlier step."
                                step.completed_at = now
                    else:
                        pending_ids.append(run.id)
                session.commit()
        except Exception:
            logger.exception("test_execution_queue_recovery_failed")
            return
        for run_id in pending_ids:
            self.enqueue(run_id)

    def enqueue(self, run_id: UUID) -> None:
        if self._stopping:
            return
        task = self._tasks.get(run_id)
        if task is not None and not task.done():
            return
        cancel_event = asyncio.Event()
        self._cancel_events[run_id] = cancel_event
        self._tasks[run_id] = asyncio.create_task(
            self._run(run_id, cancel_event), name=f"browser-test-{run_id}"
        )

    async def cancel(self, run_id: UUID) -> bool:
        task = self._tasks.get(run_id)
        if task is not None and not task.done():
            self._cancel_events[run_id].set()
            await asyncio.gather(task, return_exceptions=True)
            return True
        return self._mark_cancelled(run_id)

    async def stop(self) -> None:
        self._stopping = True
        tasks = list(self._tasks.values())
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _run(self, run_id: UUID, cancel_event: asyncio.Event) -> None:
        try:
            async with self._semaphore:
                if cancel_event.is_set():
                    self._finish_cancelled(run_id)
                    return
                await self._execute(run_id, cancel_event)
        except asyncio.CancelledError:
            self._finish_cancelled(run_id)
            raise
        except Exception:
            logger.exception("test_execution_worker_crashed", extra={"test_run_id": str(run_id)})
            self._finish_failed(run_id, "The browser test worker encountered an unexpected error.")
        finally:
            self._tasks.pop(run_id, None)
            self._cancel_events.pop(run_id, None)

    async def _execute(self, run_id: UUID, cancel_event: asyncio.Event) -> None:
        with self.session_factory() as session:
            run = session.scalar(
                select(TestRun).options(selectinload(TestRun.steps)).where(TestRun.id == run_id)
            )
            if run is None or run.status != "pending":
                return
            exploration = session.get(Exploration, run.exploration_id)
            test_case = session.get(TestCase, run.test_case_id) if run.test_case_id else None
            pages = list(
                session.scalars(
                    select(ExplorationPage)
                    .options(selectinload(ExplorationPage.elements))
                    .where(ExplorationPage.exploration_id == run.exploration_id)
                    .order_by(ExplorationPage.depth, ExplorationPage.discovered_at, ExplorationPage.url)
                )
            )
            if (
                exploration is None
                or exploration.status != "completed"
                or (run.test_case_id is not None and (
                    test_case is None or test_case.exploration_id != run.exploration_id
                ))
            ):
                self._fail_in_session(session, run, "The saved exploration or test case association is no longer available.")
                return
            try:
                observation_map = load_interaction_observations(
                    session,
                    run.scenario.get("scenario", {}) if isinstance(run.scenario, dict) else {},
                )
                compiled = _compile_saved_run_snapshot(
                    run,
                    pages,
                    exploration.start_url,
                    observation_map,
                )
            except Exception:
                message = (
                    "The saved test run snapshot no longer passes validation against persisted page and element evidence."
                )
                self._fail_in_session(session, run, message)
                return

            run.status = "running"
            run.started_at = datetime.now(UTC)
            run.error_message = None
            session.commit()
            start_url = exploration.start_url
            browser_pages = [
                {"id": str(page.id), "url": page.url, "title": page.title}
                for page in pages
            ]
            browser_steps = [
                {"action": step.action}
                for step in run.steps
            ]

        async def on_step_started(ordinal: int) -> None:
            if cancel_event.is_set():
                raise asyncio.CancelledError
            with self.session_factory() as session:
                run = session.get(TestRun, run_id)
                step = session.scalar(
                    select(TestRunStep).where(
                        TestRunStep.test_run_id == run_id,
                        TestRunStep.ordinal == ordinal,
                    )
                )
                if run is None or run.status != "running" or step is None or step.status != "pending":
                    raise asyncio.CancelledError
                step.status = "running"
                step.started_at = datetime.now(UTC)
                session.commit()

        async def on_step_finished(
            ordinal: int,
            status_value: str,
            duration_ms: int,
            error_message: str | None,
            evidence: dict | None,
        ) -> None:
            with self.session_factory() as session:
                step = session.scalar(
                    select(TestRunStep).where(
                        TestRunStep.test_run_id == run_id,
                        TestRunStep.ordinal == ordinal,
                    )
                )
                if step is None or step.status != "running":
                    return
                step.status = status_value
                step.duration_ms = duration_ms
                step.error_message = error_message[:1000] if error_message else None
                step.actual_evidence = evidence
                step.completed_at = datetime.now(UTC)
                session.commit()

        try:
            engine = self.engine_factory()
            await engine.execute_test_plan(
                start_url=start_url,
                saved_pages=browser_pages,
                steps=browser_steps,
                on_step_started=on_step_started,
                on_step_finished=on_step_finished,
                is_cancelled=cancel_event.is_set,
            )
        except asyncio.CancelledError:
            self._finish_cancelled(run_id)
            return
        except Exception as exc:
            logger.warning(
                "test_execution_browser_start_failed",
                extra={"test_run_id": str(run_id), "error_type": type(exc).__name__},
            )
            self._fail_unfinished_steps(
                run_id,
                "The browser test could not start or finish safely.",
            )
            self._finish_failed(run_id, "The browser test could not start or finish safely.")
            return

        if cancel_event.is_set():
            self._finish_cancelled(run_id)
            return
        self._finish_from_steps(run_id)

    @staticmethod
    def _fail_in_session(session: Session, run: TestRun, message: str) -> None:
        now = datetime.now(UTC)
        run.status = "failed"
        run.error_message = message
        run.completed_at = now
        for step in run.steps:
            if step.status == "pending":
                step.status = "failed" if step.ordinal == 0 else "cancelled"
                step.error_message = message if step.ordinal == 0 else "Not run because execution validation failed."
                step.completed_at = now
        session.commit()

    def _finish_from_steps(self, run_id: UUID) -> None:
        with self.session_factory() as session:
            run = session.scalar(
                select(TestRun).options(selectinload(TestRun.steps)).where(TestRun.id == run_id)
            )
            if run is None or run.status != "running":
                return
            now = datetime.now(UTC)
            failed = next((step for step in run.steps if step.status == "failed"), None)
            if failed is not None:
                run.status = "failed"
                run.error_message = f"Step {failed.ordinal + 1} failed: {failed.error_message or 'browser assertion failed'}"
                for step in run.steps:
                    if step.status == "pending":
                        step.status = "cancelled"
                        step.error_message = "Not run because an earlier step failed."
                        step.completed_at = now
            elif run.steps and all(step.status == "passed" for step in run.steps):
                run.status = "passed"
                run.error_message = None
            else:
                run.status = "failed"
                run.error_message = "The browser worker returned without completing every saved step."
                for step in run.steps:
                    if step.status in {"pending", "running"}:
                        step.status = "failed" if step.status == "running" else "cancelled"
                        step.error_message = run.error_message
                        step.completed_at = now
            run.completed_at = now
            session.commit()

    def _fail_unfinished_steps(self, run_id: UUID, message: str) -> None:
        with self.session_factory() as session:
            steps = list(
                session.scalars(
                    select(TestRunStep)
                    .where(TestRunStep.test_run_id == run_id)
                    .order_by(TestRunStep.ordinal)
                )
            )
            now = datetime.now(UTC)
            first_unfinished_assigned = False
            for step in steps:
                if step.status == "running":
                    step.status = "failed"
                    step.error_message = message
                    step.completed_at = now
                elif step.status == "pending":
                    if not first_unfinished_assigned:
                        step.status = "failed"
                        step.error_message = message
                        first_unfinished_assigned = True
                    else:
                        step.status = "cancelled"
                        step.error_message = "Not run because the browser could not continue."
                    step.completed_at = now
            session.commit()

    def _finish_failed(self, run_id: UUID, message: str) -> None:
        try:
            with self.session_factory() as session:
                run = session.get(TestRun, run_id)
                if run is None or run.status not in {"pending", "running"}:
                    return
                run.status = "failed"
                run.error_message = message[:1000]
                run.completed_at = datetime.now(UTC)
                session.commit()
        except Exception:
            logger.exception("test_execution_failure_persist_failed")

    def _finish_cancelled(self, run_id: UUID) -> None:
        try:
            with self.session_factory() as session:
                run = session.scalar(
                    select(TestRun).options(selectinload(TestRun.steps)).where(TestRun.id == run_id)
                )
                if run is None or run.status not in {"pending", "running"}:
                    return
                now = datetime.now(UTC)
                run.status = "cancelled"
                run.error_message = "The test run was cancelled."
                run.completed_at = now
                for step in run.steps:
                    if step.status in {"pending", "running"}:
                        step.status = "cancelled"
                        step.duration_ms = _duration_since(step.started_at, now)
                        step.error_message = "The test run was cancelled before this step passed."
                        step.completed_at = now
                session.commit()
        except Exception:
            logger.exception("test_execution_cancellation_persist_failed")

    def _mark_cancelled(self, run_id: UUID) -> bool:
        try:
            with self.session_factory() as session:
                run = session.scalar(
                    select(TestRun).options(selectinload(TestRun.steps)).where(TestRun.id == run_id)
                )
                if run is None or run.status not in {"pending", "running"}:
                    return False
                now = datetime.now(UTC)
                run.status = "cancelled"
                run.error_message = "The test run was cancelled."
                run.completed_at = now
                for step in run.steps:
                    if step.status in {"pending", "running"}:
                        step.status = "cancelled"
                        step.duration_ms = _duration_since(step.started_at, now)
                        step.error_message = "The test run was cancelled before this step passed."
                        step.completed_at = now
                session.commit()
                return True
        except Exception:
            logger.exception("test_execution_cancellation_persist_failed")
            return False
