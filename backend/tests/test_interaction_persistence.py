from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models.exploration import Exploration, ExplorationPage, ObservedElement
from app.models.interaction_observation import InteractionObservation
from app.services.exploration_worker import persist_interaction_observation
from app.services.interaction_discovery import InteractionPersistenceError
from app import models  # noqa: F401 - register model metadata


def _fixture():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    exploration_id = uuid4()
    page_id = uuid4()
    with session_factory() as session:
        session.add(
            Exploration(
                id=exploration_id,
                start_url="https://fixture.example/",
                status="running",
                max_pages=1,
                max_depth=0,
            )
        )
        session.add(
            ExplorationPage(
                id=page_id,
                exploration_id=exploration_id,
                url="https://fixture.example/",
                title="Fixture",
                depth=0,
            )
        )
        session.add(
            ObservedElement(
                id=uuid4(), page_id=page_id, ordinal=0, kind="button", tag_name="button",
                role="button", accessible_name="More details", attributes={},
            )
        )
        session.commit()
    return engine, session_factory, exploration_id, page_id


def _observation(outcome: str, key: str = "b" * 64) -> dict:
    return {
        "element_ordinal": 0,
        "probe_key": key,
        "action_type": "disclosure",
        "outcome": outcome,
        "reason": None if outcome == "observed" else "interaction_timeout",
        "before_evidence": {
            "url": "https://fixture.example/",
            "target": {"tag_name": "button", "role": "button", "name": "More details", "expanded": False},
        },
        "after_evidence": None,
        "duration_ms": 15,
    }


def test_terminal_observation_upsert_is_idempotent_and_keeps_verified_state() -> None:
    engine, session_factory, exploration_id, page_id = _fixture()
    try:
        persist_interaction_observation(session_factory, exploration_id, page_id, _observation("observed"))
        # A replay after worker recovery cannot downgrade an already verified
        # transition or insert a duplicate observation.
        persist_interaction_observation(session_factory, exploration_id, page_id, _observation("failed"))
        with session_factory() as session:
            rows = list(session.scalars(select(InteractionObservation)))
            assert len(rows) == 1
            assert rows[0].outcome == "observed"
            assert session.scalar(select(func.count()).select_from(InteractionObservation)) == 1
    finally:
        engine.dispose()


def test_non_success_outcome_can_be_recovered_by_a_later_terminal_result() -> None:
    engine, session_factory, exploration_id, page_id = _fixture()
    try:
        persist_interaction_observation(session_factory, exploration_id, page_id, _observation("failed"))
        recovered = _observation("blocked")
        recovered["reason"] = "non_read_only_method"
        persist_interaction_observation(session_factory, exploration_id, page_id, recovered)
        with session_factory() as session:
            rows = list(session.scalars(select(InteractionObservation)))
            assert len(rows) == 1
            assert rows[0].outcome == "blocked"
            assert rows[0].reason == "non_read_only_method"
    finally:
        engine.dispose()


def test_persistence_error_is_not_hidden_as_a_success() -> None:
    def broken_session_factory():
        raise RuntimeError("database unavailable")

    with pytest.raises(InteractionPersistenceError):
        persist_interaction_observation(
            broken_session_factory, uuid4(), uuid4(), _observation("observed")
        )
