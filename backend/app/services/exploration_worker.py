"""In-process asynchronous queue that isolates browser work from HTTP handlers."""

import asyncio
import logging
import sys
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings
from app.models.exploration import (
    Exploration,
    ExplorationEvent,
    ExplorationPage,
    ObservedElement,
)
from app.models.interaction_observation import InteractionObservation
from app.services.browser_explorer import BrowserExplorationEngine, ExtractedPage, RootPageError
from app.services.interaction_discovery import InteractionPersistenceError

logger = logging.getLogger("software_intelligence.exploration")


def append_event(
    session: Session,
    exploration: Exploration,
    event_type: str,
    message: str,
    url: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    exploration.event_count += 1
    session.add(
        ExplorationEvent(
            exploration_id=exploration.id,
            sequence=exploration.event_count,
            event_type=event_type,
            message=message,
            url=url,
            details=details or {},
        )
    )


def persist_page(session_factory: sessionmaker[Session], exploration_id: UUID, page: ExtractedPage) -> None:
    with session_factory() as session:
        exploration = session.get(Exploration, exploration_id)
        if exploration is None or exploration.status not in {"running", "queued"}:
            return
        existing = session.scalar(
            select(ExplorationPage).where(
                ExplorationPage.exploration_id == exploration_id,
                ExplorationPage.url == page.url,
            )
        )
        old_count = existing.element_count if existing else 0
        if existing is None:
            existing = ExplorationPage(exploration_id=exploration_id, url=page.url, depth=page.depth)
            session.add(existing)
            session.flush()
            exploration.pages_discovered += 1
        else:
            for element in list(existing.elements):
                session.delete(element)

        existing.title = page.title
        existing.description = page.description
        existing.depth = page.depth
        existing.http_status = page.http_status
        existing.visible_text = page.visible_text
        existing.dom_outline = page.dom_outline
        existing.screenshot = page.screenshot
        existing.screenshot_mime = "image/jpeg" if page.screenshot else None
        existing.element_count = len(page.elements)
        page.persisted_id = existing.id
        exploration.elements_discovered += len(page.elements) - old_count
        for element in page.elements:
            persisted_element = ObservedElement(
                id=uuid4(),
                page_id=existing.id,
                ordinal=element.ordinal,
                kind=element.kind,
                tag_name=element.tag_name,
                role=element.role,
                accessible_name=element.accessible_name,
                label=element.label,
                text=element.text,
                selector=element.selector,
                attributes=element.attributes,
            )
            session.add(persisted_element)
            element.persisted_id = persisted_element.id
        session.commit()


def persist_interaction_observation(
    session_factory: sessionmaker[Session],
    exploration_id: UUID,
    page_id: UUID,
    observation: dict[str, Any],
) -> None:
    """Upsert one terminal outcome; never persist an in-flight probe as success."""
    try:
        with session_factory() as session:
            exploration = session.get(Exploration, exploration_id)
            page = session.get(ExplorationPage, page_id)
            if (
                exploration is None
                or exploration.status != "running"
                or page is None
                or page.exploration_id != exploration_id
            ):
                raise InteractionPersistenceError("The exploration page is no longer active.")
            ordinal_exists = session.scalar(
                select(ObservedElement.id).where(
                    ObservedElement.page_id == page_id,
                    ObservedElement.ordinal == observation["element_ordinal"],
                )
            )
            if ordinal_exists is None:
                raise InteractionPersistenceError("The observed control is no longer persisted.")

            existing = session.scalar(
                select(InteractionObservation).where(
                    InteractionObservation.exploration_id == exploration_id,
                    InteractionObservation.page_id == page_id,
                    InteractionObservation.probe_key == observation["probe_key"],
                )
            )
            # A previous verified transition is stronger evidence than a later
            # interrupted retry; keep it stable and avoid duplicate rows.
            if existing is not None and existing.outcome == "observed":
                return
            if existing is None:
                existing = InteractionObservation(
                    exploration_id=exploration_id,
                    page_id=page_id,
                    element_ordinal=observation["element_ordinal"],
                    probe_key=observation["probe_key"],
                    action_type=observation["action_type"],
                    outcome=observation["outcome"],
                    reason=observation["reason"],
                    before_evidence=observation["before_evidence"],
                    after_evidence=observation["after_evidence"],
                    duration_ms=observation["duration_ms"],
                )
                session.add(existing)
            else:
                existing.element_ordinal = observation["element_ordinal"]
                existing.action_type = observation["action_type"]
                existing.outcome = observation["outcome"]
                existing.reason = observation["reason"]
                existing.before_evidence = observation["before_evidence"]
                existing.after_evidence = observation["after_evidence"]
                existing.duration_ms = observation["duration_ms"]
            try:
                session.commit()
            except IntegrityError:
                # A duplicate terminal write can race after an in-process or
                # deployment restart. Resolve the unique key and keep one row.
                session.rollback()
                existing = session.scalar(
                    select(InteractionObservation).where(
                        InteractionObservation.exploration_id == exploration_id,
                        InteractionObservation.page_id == page_id,
                        InteractionObservation.probe_key == observation["probe_key"],
                    )
                )
                if existing is None:
                    raise
                if existing.outcome != "observed":
                    existing.element_ordinal = observation["element_ordinal"]
                    existing.action_type = observation["action_type"]
                    existing.outcome = observation["outcome"]
                    existing.reason = observation["reason"]
                    existing.before_evidence = observation["before_evidence"]
                    existing.after_evidence = observation["after_evidence"]
                    existing.duration_ms = observation["duration_ms"]
                    session.commit()
    except InteractionPersistenceError:
        raise
    except Exception as exc:
        logger.exception("interaction_observation_persist_failed", extra={"exploration_id": str(exploration_id)})
        raise InteractionPersistenceError("Interaction observation persistence failed.") from exc


class ExplorationManager:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: Settings | None = None,
        engine_factory: Callable[[], BrowserExplorationEngine] | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings or get_settings()
        self.engine_factory = engine_factory or (lambda: BrowserExplorationEngine(self.settings))
        self._semaphore = asyncio.Semaphore(self.settings.exploration_worker_concurrency)
        self._tasks: dict[UUID, asyncio.Task[None]] = {}
        self._cancel_events: dict[UUID, threading.Event] = {}
        self._stopping = False

    async def start(self) -> None:
        self._stopping = False
        try:
            with self.session_factory() as session:
                interrupted = list(
                    session.scalars(
                        select(Exploration).where(
                            Exploration.status.in_(("queued", "running"))
                        )
                    )
                )
                to_enqueue: list[UUID] = []
                for exploration in interrupted:
                    if exploration.status == "running":
                        exploration.status = "queued"
                        append_event(
                            session,
                            exploration,
                            "worker_recovered",
                            "The previous worker stopped; the exploration was requeued.",
                            exploration.current_url,
                        )
                    to_enqueue.append(exploration.id)
                session.commit()
        except Exception:
            logger.exception("exploration_queue_recovery_failed")
            return
        for exploration_id in to_enqueue:
            self.enqueue(exploration_id)

    def enqueue(self, exploration_id: UUID) -> None:
        if self._stopping or exploration_id in self._tasks:
            return
        cancel_event = threading.Event()
        self._cancel_events[exploration_id] = cancel_event
        self._tasks[exploration_id] = asyncio.create_task(
            self._run(exploration_id, cancel_event), name=f"exploration-{exploration_id}"
        )

    async def cancel(self, exploration_id: UUID) -> bool:
        task = self._tasks.get(exploration_id)
        if task is None:
            return False
        self._cancel_events[exploration_id].set()
        await asyncio.gather(task, return_exceptions=True)
        return True

    async def stop(self) -> None:
        self._stopping = True
        tasks = list(self._tasks.values())
        for cancel_event in self._cancel_events.values():
            cancel_event.set()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _run(
        self, exploration_id: UUID, cancel_event: threading.Event
    ) -> None:
        try:
            async with self._semaphore:
                if cancel_event.is_set():
                    raise asyncio.CancelledError
                await self._execute(exploration_id, cancel_event)
        except asyncio.CancelledError:
            self._finish_interrupted(exploration_id, requeue=self._stopping)
        except Exception:
            logger.exception(
                "exploration_worker_crashed",
                extra={"exploration_id": str(exploration_id)},
            )
            self._finish_failed(exploration_id, "The exploration worker encountered an unexpected error.")
        finally:
            self._tasks.pop(exploration_id, None)
            self._cancel_events.pop(exploration_id, None)

    async def _execute(
        self, exploration_id: UUID, cancel_event: threading.Event
    ) -> None:
        with self.session_factory() as session:
            exploration = session.get(Exploration, exploration_id)
            if exploration is None or exploration.status != "queued":
                return
            exploration.status = "running"
            exploration.started_at = exploration.started_at or datetime.now(UTC)
            exploration.error_message = None
            append_event(session, exploration, "exploration_started", "Browser exploration started.", exploration.start_url)
            start_url = exploration.start_url
            session.commit()

        def is_cancelled() -> bool:
            return cancel_event.is_set()

        async def on_page(page: ExtractedPage) -> None:
            if is_cancelled():
                raise asyncio.CancelledError
            persist_page(self.session_factory, exploration_id, page)

        async def on_interaction(page_id: UUID, observation: dict[str, Any]) -> None:
            # The engine saves only terminal outcomes. Persist a cancellation
            # failure before the manager marks the exploration cancelled.
            persist_interaction_observation(
                self.session_factory,
                exploration_id,
                page_id,
                observation,
            )

        async def on_event(
            event_type: str,
            message: str,
            url: str | None,
            details: dict[str, Any],
        ) -> None:
            if is_cancelled():
                raise asyncio.CancelledError
            with self.session_factory() as session:
                exploration = session.get(Exploration, exploration_id)
                if exploration is None or exploration.status != "running":
                    return
                if event_type == "navigation_started":
                    exploration.current_url = url
                append_event(session, exploration, event_type, message, url, details)
                session.commit()

        async def run_browser_engine() -> None:
            await self.engine_factory().explore(
                start_url, on_page, on_event, is_cancelled, on_interaction
            )

        def run_in_browser_thread() -> None:
            if sys.platform == "win32":
                with asyncio.Runner(loop_factory=asyncio.ProactorEventLoop) as runner:
                    runner.run(run_browser_engine())
            else:
                asyncio.run(run_browser_engine())

        try:
            await asyncio.to_thread(run_in_browser_thread)
        except asyncio.CancelledError:
            raise
        except RootPageError as exc:
            self._finish_failed(exploration_id, str(exc))
            return
        except Exception as exc:
            logger.exception(
                "browser_exploration_failed",
                extra={"exploration_id": str(exploration_id)},
            )
            self._finish_failed(exploration_id, "The browser exploration failed.")
            return

        with self.session_factory() as session:
            exploration = session.get(Exploration, exploration_id)
            if exploration is None or exploration.status != "running":
                return
            exploration.status = "completed"
            exploration.completed_at = datetime.now(UTC)
            append_event(
                session,
                exploration,
                "exploration_completed",
                "Browser exploration completed.",
                exploration.current_url,
                {"pages_discovered": exploration.pages_discovered, "elements_discovered": exploration.elements_discovered},
            )
            session.commit()

    def _finish_interrupted(self, exploration_id: UUID, requeue: bool) -> None:
        try:
            with self.session_factory() as session:
                exploration = session.get(Exploration, exploration_id)
                if exploration is None or exploration.status not in {"queued", "running"}:
                    return
                if requeue:
                    exploration.status = "queued"
                    append_event(
                        session,
                        exploration,
                        "worker_stopped",
                        "The backend is stopping; the exploration remains queued for restart.",
                        exploration.current_url,
                    )
                else:
                    exploration.status = "cancelled"
                    exploration.completed_at = datetime.now(UTC)
                    append_event(
                        session,
                        exploration,
                        "exploration_cancelled",
                        "Exploration was cancelled by the user.",
                        exploration.current_url,
                    )
                session.commit()
        except Exception:
            logger.exception("exploration_interruption_persist_failed")

    def _finish_failed(self, exploration_id: UUID, message: str) -> None:
        try:
            with self.session_factory() as session:
                exploration = session.get(Exploration, exploration_id)
                if exploration is None or exploration.status != "running":
                    return
                exploration.status = "failed"
                exploration.error_message = message[:2000]
                exploration.completed_at = datetime.now(UTC)
                append_event(session, exploration, "exploration_failed", message[:1000], exploration.current_url)
                session.commit()
        except Exception:
            logger.exception("exploration_failure_persist_failed")
