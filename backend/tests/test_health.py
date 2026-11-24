from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.api.v1.health import get_public_db_session
from app.core.config import Settings
from app.main import create_app
from tests.conftest import FakeExplorationManager


def test_health_reports_database_connection(client: TestClient) -> None:
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "connected"}
    assert response.headers.get("x-request-id")


def test_health_reports_database_failure_as_service_unavailable() -> None:
    application = create_app(
        manager_factory=FakeExplorationManager,
        settings=Settings(_env_file=None),
    )

    def unavailable_database() -> None:
        raise OperationalError("SELECT 1", {}, ConnectionError("offline"))

    application.dependency_overrides[get_public_db_session] = unavailable_database
    with TestClient(application, raise_server_exceptions=False) as client:
        response = client.get("/api/v1/health")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "database_unavailable"


def test_cors_allows_configured_vite_origin(client: TestClient) -> None:
    response = client.options(
        "/api/v1/health",
        headers={
            "Origin": "http://localhost:8443",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:8443"
