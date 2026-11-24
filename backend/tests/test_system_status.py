from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.api.v1 import system
from app.core.config import Settings


def test_system_status_reports_runtime_and_safe_configuration(client: TestClient, monkeypatch) -> None:
    client.app.state.settings = Settings(
        _env_file=None,
        app_environment="development",
        ai_api_base_url="https://provider.example/v1",
        ai_model="example-model",
        ai_api_key="must-not-be-returned",
    )
    monkeypatch.setattr(system, "browser_automation_available", lambda _settings: True)

    response = client.get("/api/v1/system/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload == {
        "version": "0.1.0",
        "environment": "development",
        "database": "healthy",
        "ai_provider": "configured",
        "browser_automation": "available",
    }
    assert "must-not-be-returned" not in response.text
    assert "provider.example" not in response.text
    assert "example-model" not in response.text


def test_system_status_distinguishes_unhealthy_database_and_unconfigured_ai(client: TestClient, monkeypatch) -> None:
    client.app.state.settings = Settings(_env_file=None)

    def unavailable_database(session) -> None:
        raise OperationalError("SELECT 1", {}, ConnectionError("offline"))

    monkeypatch.setattr(system, "check_database", unavailable_database)
    monkeypatch.setattr(system, "browser_automation_available", lambda _settings: False)

    response = client.get("/api/v1/system/status")

    assert response.status_code == 200
    assert response.json()["database"] == "unhealthy"
    assert response.json()["ai_provider"] == "not_configured"
    assert response.json()["browser_automation"] == "unavailable"


def test_browser_automation_status_checks_the_configured_chromium_executable(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(system, "find_spec", lambda _name: object())
    monkeypatch.setattr(system.sys, "platform", "win32")
    executable = tmp_path / "chromium-1234" / "chrome-win64" / "chrome.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"controlled fixture")

    assert system.browser_automation_available(
        Settings(_env_file=None, playwright_browsers_path=tmp_path)
    )
