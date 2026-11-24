from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_settings_load_from_env_file(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("APP_NAME", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    monkeypatch.delenv("APP_ENVIRONMENT", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "APP_NAME=Dotenv Test API",
                "DATABASE_URL=postgresql+psycopg://test-user:test-password@localhost:5432/test_db",
                'CORS_ORIGINS=["http://localhost:8443"]',
                "LOG_LEVEL=DEBUG",
            ]
        ),
        encoding="utf-8",
    )

    settings = Settings(_env_file=env_file)

    assert settings.app_name == "Dotenv Test API"
    assert settings.database_url.endswith("/test_db")
    assert "test-password" not in repr(settings)
    assert settings.cors_origins == ["http://localhost:8443"]
    assert settings.log_level == "DEBUG"


def test_optional_ai_provider_settings_load_from_env_file(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "AI_API_BASE_URL=http://localhost:11434/v1",
                "AI_API_KEY=local-test-key",
                "AI_MODEL=local-model",
            ]
        ),
        encoding="utf-8",
    )

    settings = Settings(_env_file=env_file)

    assert settings.ai_api_base_url == "http://localhost:11434/v1"
    assert settings.ai_api_key is not None
    assert settings.ai_api_key.get_secret_value() == "local-test-key"
    assert settings.ai_model == "local-model"
    assert "local-test-key" not in repr(settings.ai_api_key)


def test_gmail_smtp_settings_load_with_starttls_and_authenticated_sender(tmp_path: Path, monkeypatch) -> None:
    names = (
        "SMTP_HOST", "SMTP_PORT", "SMTP_USERNAME", "SMTP_PASSWORD", "SMTP_FROM_EMAIL",
        "SMTP_USE_TLS", "SMTP_USE_SSL",
    )
    for name in names:
        monkeypatch.delenv(name, raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join((
            "SMTP_HOST=smtp.gmail.com",
            "SMTP_PORT=587",
            "SMTP_USERNAME=sender@gmail.com",
            "SMTP_PASSWORD=test-app-password",
            "SMTP_FROM_EMAIL=SID <sender@gmail.com>",
            "SMTP_USE_TLS=true",
            "SMTP_USE_SSL=false",
        )),
        encoding="utf-8",
    )

    settings = Settings(_env_file=env_file)

    assert settings.smtp_host == "smtp.gmail.com"
    assert settings.smtp_port == 587
    assert settings.smtp_use_tls is True
    assert settings.smtp_use_ssl is False
    assert settings.email_delivery_configured is True
    assert "test-app-password" not in repr(settings.smtp_password)


def test_smtp_is_unavailable_when_tls_and_ssl_are_both_enabled() -> None:
    settings = Settings(
        _env_file=None,
        smtp_host="smtp.gmail.com",
        smtp_username="sender@gmail.com",
        smtp_password="test-app-password",
        smtp_from_email="sender@gmail.com",
        smtp_use_tls=True,
        smtp_use_ssl=True,
    )

    assert settings.email_delivery_configured is False


def test_gmail_recovery_is_unavailable_without_authentication_credentials() -> None:
    settings = Settings(
        _env_file=None,
        smtp_host="smtp.gmail.com",
        smtp_port=587,
        smtp_from_email="sender@gmail.com",
        smtp_use_tls=True,
        smtp_use_ssl=False,
    )

    assert settings.email_delivery_configured is False


def test_cors_origins_are_empty_by_default() -> None:
    settings = Settings(_env_file=None)

    assert settings.cors_origins == []


@pytest.mark.parametrize(
    "origin",
    [
        "http://localhost:8443",
        "https://localhost:8443",
        "https://127.0.0.1:8443",
        "https://*.example.com",
        "https://dashboard.example.com/path",
    ],
)
def test_production_rejects_development_or_non_origin_cors_values(origin: str) -> None:
    with pytest.raises(ValidationError, match="Production CORS origins"):
        Settings(
            _env_file=None,
            app_environment="production",
            database_url="postgresql+psycopg://app@db.example.test:5432/software_intelligence",
            cors_origins=[origin],
        )


def test_production_accepts_an_explicit_https_frontend_origin() -> None:
    settings = Settings(
        _env_file=None,
        app_environment="production",
        database_url="postgresql+psycopg://app@db.example.test:5432/software_intelligence",
        cors_origins=["https://dashboard.example.com"],
        frontend_base_url="https://dashboard.example.com",
    )

    assert settings.cors_origins == ["https://dashboard.example.com"]


def test_production_rejects_insecure_or_credential_bearing_ai_provider_urls() -> None:
    for provider_url in (
        "http://ai.example.com/v1",
        "https://user:password@ai.example.com/v1",
        "https://ai.example.com/v1?token=secret",
    ):
        with pytest.raises(ValidationError, match="Production AI_API_BASE_URL"):
            Settings(
                _env_file=None,
                app_environment="production",
                database_url="postgresql+psycopg://app@db.example.test:5432/software_intelligence",
                ai_api_base_url=provider_url,
                frontend_base_url="https://dashboard.example.com",
            )


def test_production_accepts_an_explicit_https_ai_provider_url() -> None:
    settings = Settings(
        _env_file=None,
        app_environment="production",
        database_url="postgresql+psycopg://app@db.example.test:5432/software_intelligence",
        ai_api_base_url="https://ai.example.com/v1",
        frontend_base_url="https://dashboard.example.com",
    )

    assert settings.ai_api_base_url == "https://ai.example.com/v1"


def test_production_rejects_the_development_database_default() -> None:
    with pytest.raises(ValidationError, match="Production requires an explicit DATABASE_URL"):
        Settings(_env_file=None, app_environment="production")
