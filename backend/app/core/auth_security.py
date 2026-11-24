from __future__ import annotations

import hashlib
import re
import smtplib
from email.message import EmailMessage
from urllib.parse import urlsplit

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import Settings

_PASSWORDS = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)
_DUMMY_PASSWORD_HASH = _PASSWORDS.hash("inscope-invalid-account-password-dummy")
_EMAIL_PATTERN = re.compile(r"^[^\s@]{1,64}@[^\s@.]+(?:\.[^\s@.]+)+$")


def normalize_email(value: str) -> str:
    normalized = value.strip().casefold()
    if len(normalized) > 320 or not _EMAIL_PATTERN.fullmatch(normalized):
        raise ValueError("Enter a valid email address.")
    return normalized


def hash_password(value: str) -> str:
    return _PASSWORDS.hash(value)


def verify_password(value: str, password_hash: str | None) -> bool:
    stored = password_hash or _DUMMY_PASSWORD_HASH
    try:
        valid = _PASSWORDS.verify(stored, value)
        return bool(valid and password_hash)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def token_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def safe_redirect_path(value: str | None, default: str = "/") -> str:
    if not value or len(value) > 2048 or "\\" in value or any(ord(char) < 32 for char in value):
        return default
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or not parsed.path.startswith("/") or parsed.path.startswith("//"):
        return default
    if parsed.path in {"/login", "/register", "/forgot-password", "/reset-password", "/verify-email", "/welcome"}:
        return default
    return value


def csrf_origin_allowed(origin: str | None, settings: Settings) -> bool:
    if not origin:
        return False
    if origin in settings.cors_origins:
        return True
    if settings.app_environment == "development":
        parsed = urlsplit(origin)
        return parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"} and parsed.port is not None
    return False


def send_email(settings: Settings, recipient: str, subject: str, body: str) -> None:
    if not settings.email_delivery_configured:
        raise RuntimeError("Email delivery is not configured.")
    message = EmailMessage()
    message["From"] = settings.smtp_from_email
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)
    if settings.smtp_use_ssl:
        with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=settings.smtp_timeout_seconds) as smtp:
            _send_smtp(smtp, settings, message)
    else:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=settings.smtp_timeout_seconds) as smtp:
            if settings.smtp_use_tls:
                smtp.starttls()
            _send_smtp(smtp, settings, message)


def _send_smtp(smtp: smtplib.SMTP, settings: Settings, message: EmailMessage) -> None:
    if settings.smtp_username:
        smtp.login(settings.smtp_username, settings.smtp_password.get_secret_value() if settings.smtp_password else "")
    smtp.send_message(message)
