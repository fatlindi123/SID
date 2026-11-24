from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.v1.health import get_db_session, get_public_db_session
from app.core.config import Settings
from app.db.base import Base
from app.models.auth import AuthBootstrap
from app.main import create_app
from app import models  # noqa: F401 - register test metadata


class FakeExplorationManager:
    def __init__(self) -> None:
        self.queued: list[object] = []

    async def start(self) -> None:
        return None

    def enqueue(self, exploration_id: object) -> None:
        self.queued.append(exploration_id)

    async def cancel(self, exploration_id: object) -> bool:
        return False

    async def stop(self) -> None:
        return None


class FakeAnalysisManager:
    def __init__(self) -> None:
        self.queued: list[object] = []

    async def start(self) -> None:
        return None

    def enqueue(self, exploration_id: object) -> None:
        self.queued.append(exploration_id)

    async def stop(self) -> None:
        return None


class FakeTestExecutionManager:
    def __init__(self) -> None:
        self.queued: list[object] = []

    async def start(self) -> None:
        return None

    def enqueue(self, run_id: object) -> None:
        self.queued.append(run_id)

    async def cancel(self, run_id: object) -> bool:
        return True

    async def stop(self) -> None:
        return None


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    test_engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    test_session_factory = sessionmaker(
        bind=test_engine,
        autoflush=False,
        expire_on_commit=False,
    )
    Base.metadata.create_all(test_engine)
    with test_session_factory.begin() as session:
        session.add(AuthBootstrap(id=1))
    test_manager = FakeExplorationManager()
    test_analysis_manager = FakeAnalysisManager()
    test_execution_manager = FakeTestExecutionManager()
    application = create_app(
        manager_factory=lambda: test_manager,
        analysis_manager_factory=lambda: test_analysis_manager,
        test_execution_manager_factory=lambda: test_execution_manager,
        settings=Settings(_env_file=None, cors_origins=["http://localhost:8443"]),
    )
    application.state.test_session_factory = test_session_factory
    application.state.fake_test_execution_manager = test_execution_manager

    def override_db_session() -> Generator[Session, None, None]:
        with test_session_factory() as session:
            yield session

    def override_public_db_session() -> Generator[Session, None, None]:
        with test_session_factory() as session:
            yield session

    application.dependency_overrides[get_db_session] = override_db_session
    application.dependency_overrides[get_public_db_session] = override_public_db_session
    with TestClient(application) as test_client:
        yield test_client
    application.dependency_overrides.clear()
    test_engine.dispose()
