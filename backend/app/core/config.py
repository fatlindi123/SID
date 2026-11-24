from functools import lru_cache
from ipaddress import ip_address
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DEVELOPMENT_DATABASE_URL = "postgresql+psycopg://localhost:5432/software_intelligence"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "SID API"
    app_environment: Literal["development", "production"] = "development"
    api_v1_prefix: str = "/api/v1"
    # Uses the local PostgreSQL account when the server allows it. Set DATABASE_URL
    # in backend/.env for installations that require an explicit database user.
    database_url: str = Field(
        default=DEFAULT_DEVELOPMENT_DATABASE_URL,
        repr=False,
    )
    cors_origins: list[str] = Field(default_factory=list)
    log_level: str = "INFO"
    playwright_browsers_path: Path = BACKEND_ROOT / ".cache" / "playwright"
    exploration_max_pages: int = Field(default=20, ge=1, le=100)
    exploration_max_depth: int = Field(default=3, ge=0, le=10)
    exploration_page_timeout_seconds: float = Field(default=30, ge=1, le=120)
    exploration_worker_concurrency: int = Field(default=2, ge=1, le=8)
    interaction_probe_timeout_seconds: float = Field(default=8, ge=1, le=30)
    interaction_max_probes_per_page: int = Field(default=5, ge=0, le=20)
    interaction_max_probes_per_exploration: int = Field(default=30, ge=0, le=200)
    interaction_max_observations_per_exploration: int = Field(default=100, ge=1, le=500)
    interaction_max_candidates_per_page: int = Field(default=30, ge=1, le=100)
    ai_api_base_url: str | None = None
    ai_api_key: SecretStr | None = None
    ai_model: str | None = None
    ai_request_timeout_seconds: float = Field(default=60, ge=1, le=300)
    ai_analysis_worker_concurrency: int = Field(default=2, ge=1, le=8)
    ai_max_context_elements: int = Field(default=3000, ge=50, le=20000)
    frontend_base_url: str = "http://127.0.0.1:8443"
    auth_session_ttl_hours: int = Field(default=168, ge=1, le=720)
    auth_challenge_ttl_minutes: int = Field(default=30, ge=5, le=1440)
    smtp_host: str | None = None
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_username: str | None = None
    smtp_password: SecretStr | None = Field(default=None, repr=False)
    smtp_from_email: str | None = None
    smtp_use_tls: bool = True
    smtp_use_ssl: bool = False
    smtp_timeout_seconds: float = Field(default=10, ge=1, le=60)

    @property
    def email_delivery_configured(self) -> bool:
        if not self.smtp_host or not self.smtp_from_email or self.smtp_use_tls and self.smtp_use_ssl:
            return False
        has_username = bool(self.smtp_username)
        has_password = bool(self.smtp_password and self.smtp_password.get_secret_value().strip())
        if has_username != has_password:
            return False
        if self.smtp_host.lower() == "smtp.gmail.com":
            return self.smtp_port == 587 and self.smtp_use_tls and not self.smtp_use_ssl and has_username and has_password
        return True

    @model_validator(mode="after")
    def validate_production_security_settings(self) -> "Settings":
        if self.smtp_password is not None and not self.smtp_password.get_secret_value().strip():
            self.smtp_password = None
        for name in ("smtp_host", "smtp_username", "smtp_from_email"):
            value = getattr(self, name)
            if value is not None and not value.strip():
                setattr(self, name, None)
        if self.app_environment != "production":
            return self

        if self.database_url == DEFAULT_DEVELOPMENT_DATABASE_URL:
            raise ValueError("Production requires an explicit DATABASE_URL.")

        for origin in self.cors_origins:
            try:
                parsed = urlsplit(origin)
                hostname = parsed.hostname
                # Reading .port also validates malformed or out-of-range ports.
                _ = parsed.port
            except ValueError as exc:
                raise ValueError("Production CORS origins must be valid HTTPS origins.") from exc
            try:
                address = ip_address(hostname) if hostname else None
            except ValueError:
                address = None

            if (
                not origin
                or origin != origin.strip()
                or "*" in origin
                or parsed.scheme.lower() != "https"
                or not parsed.netloc
                or not hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path
                or parsed.query
                or parsed.fragment
                or hostname.lower() == "localhost"
                or hostname.lower().endswith(".localhost")
                or (address is not None and address.is_loopback)
            ):
                raise ValueError(
                    "Production CORS origins must be explicit HTTPS origins and cannot use localhost, loopback IPs, or wildcards."
                )

        if self.ai_api_base_url:
            try:
                provider_url = urlsplit(self.ai_api_base_url)
                provider_hostname = provider_url.hostname
                _ = provider_url.port
            except ValueError as exc:
                raise ValueError("Production AI_API_BASE_URL must be a valid HTTPS URL.") from exc
            if (
                provider_url.scheme.lower() != "https"
                or not provider_url.netloc
                or not provider_hostname
                or provider_url.username is not None
                or provider_url.password is not None
                or provider_url.query
                or provider_url.fragment
            ):
                raise ValueError(
                    "Production AI_API_BASE_URL must use HTTPS and cannot contain credentials, a query, or a fragment."
                )
        try:
            frontend = urlsplit(self.frontend_base_url)
            _ = frontend.port
        except ValueError as exc:
            raise ValueError("FRONTEND_BASE_URL must be a valid origin.") from exc
        if not frontend.scheme or not frontend.netloc or frontend.path or frontend.query or frontend.fragment:
            raise ValueError("FRONTEND_BASE_URL must be an origin without a path, query, or fragment.")
        if self.app_environment == "production":
            if frontend.scheme.lower() != "https":
                raise ValueError("Production FRONTEND_BASE_URL must use HTTPS.")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
