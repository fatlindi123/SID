from __future__ import annotations

import secrets
from datetime import timedelta
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.auth_security import hash_password, normalize_email, safe_redirect_path, send_email, token_hash, verify_password
from app.core.config import Settings
from app.db.session import SessionLocal
from app.models.auth import AuthChallenge, AuthSession, User
from app.schemas.auth import (
    AuthActionRead,
    AuthConfigurationRead,
    CsrfRead,
    EmailRequest,
    LoginRequest,
    LoginResponse,
    ProfileUpdateRequest,
    RegisterRequest,
    ResetPasswordRequest,
    VerifyEmailRequest,
    UserRead,
)
from app.services.auth import (
    check_rate_limit,
    claim_legacy_workspace,
    clear_session_cookies,
    create_challenge,
    current_session,
    ensure_csrf_session,
    issue_authenticated_session,
    is_expired,
    utcnow,
    validate_csrf,
)

router = APIRouter(prefix="/auth", tags=["authentication"])
_GENERIC_RECOVERY_MESSAGE = "If the address belongs to an account and email delivery is available, instructions will be sent."


def _session(request: Request):
    factory = getattr(request.app.state, "test_session_factory", SessionLocal)
    with factory() as session:
        yield session


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _preauth_session(request: Request, session: Session, settings: Settings) -> AuthSession:
    auth_session = current_session(request, session, require_user=False)
    if auth_session.user_id is not None:
        raise HTTPException(status_code=409, detail="You are already signed in.")
    validate_csrf(request, auth_session, settings)
    return auth_session


def _email_link(settings: Settings, route: str, token: str) -> str:
    return settings.frontend_base_url.rstrip("/") + route + "?" + urlencode({"token": token})


def _safe_user(user: User) -> UserRead:
    return UserRead(
        id=str(user.id),
        full_name=user.full_name,
        email=user.email,
        email_verified=user.email_verified,
        onboarding_completed=user.onboarding_completed_at is not None,
    )


@router.get("/configuration", response_model=AuthConfigurationRead)
def get_auth_configuration(request: Request) -> AuthConfigurationRead:
    settings = _settings(request)
    return AuthConfigurationRead(
        password_recovery_available=settings.email_delivery_configured,
    )


@router.get("/csrf", response_model=CsrfRead)
def get_csrf(
    request: Request,
    response: Response,
    session: Annotated[Session, Depends(_session)],
) -> CsrfRead:
    csrf_token = ensure_csrf_session(request, response, session, _settings(request))
    return CsrfRead(csrf_token=csrf_token)


@router.post("/register", response_model=LoginResponse, status_code=status.HTTP_201_CREATED)
def register(
    payload: RegisterRequest,
    request: Request,
    response: Response,
    session: Annotated[Session, Depends(_session)],
) -> LoginResponse:
    settings = _settings(request)
    _preauth_session(request, session, settings)
    check_rate_limit("register:" + (request.client.host if request.client else "unknown"), limit=8)
    existing = session.scalar(select(User).where(User.email == payload.email))
    if existing:
        raise HTTPException(status_code=409, detail="An account with this email already exists.")
    user = User(
        email=payload.email,
        full_name=payload.full_name,
        password_hash=hash_password(payload.password),
        email_verified=False,
    )
    try:
        session.add(user)
        session.flush()
        claim_legacy_workspace(session, user.id)
        csrf_token = issue_authenticated_session(request, response, session, settings, user.id)
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="An account with this email already exists.") from exc
    destination = safe_redirect_path(payload.next)
    next_path = "/welcome"
    if destination != "/":
        next_path += "?" + urlencode({"next": destination})
    return LoginResponse(user=_safe_user(user), next=next_path, csrf_token=csrf_token)


@router.post("/login", response_model=LoginResponse)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    session: Annotated[Session, Depends(_session)],
) -> LoginResponse:
    settings = _settings(request)
    _preauth_session(request, session, settings)
    check_rate_limit("login:" + (request.client.host if request.client else "unknown"), limit=12)
    user = session.scalar(select(User).where(User.email == payload.email))
    if not verify_password(payload.password, user.password_hash if user else None) or user is None:
        raise HTTPException(status_code=401, detail="Email or password is incorrect.")
    csrf_token = issue_authenticated_session(request, response, session, settings, user.id)
    session.commit()
    destination = safe_redirect_path(payload.next)
    if user.onboarding_completed_at is None:
        next_path = "/welcome"
        if destination != "/":
            next_path += "?" + urlencode({"next": destination})
    else:
        next_path = destination
    return LoginResponse(user=_safe_user(user), next=next_path, csrf_token=csrf_token)


@router.get("/me", response_model=UserRead)
def get_me(
    request: Request,
    session: Annotated[Session, Depends(_session)],
) -> UserRead:
    auth_session = current_session(request, session, require_user=True)
    user = session.get(User, auth_session.user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication is required.")
    return _safe_user(user)


@router.patch("/me", response_model=UserRead)
def update_me(
    payload: ProfileUpdateRequest,
    request: Request,
    session: Annotated[Session, Depends(_session)],
) -> UserRead:
    auth_session = current_session(request, session, require_user=True)
    validate_csrf(request, auth_session, _settings(request))
    user = session.get(User, auth_session.user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication is required.")
    user.full_name = payload.full_name
    session.commit()
    return _safe_user(user)


@router.post("/onboarding/complete", response_model=UserRead)
def complete_onboarding(
    request: Request,
    session: Annotated[Session, Depends(_session)],
) -> UserRead:
    auth_session = current_session(request, session, require_user=True)
    validate_csrf(request, auth_session, _settings(request))
    user = session.get(User, auth_session.user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication is required.")
    if user.onboarding_completed_at is None:
        user.onboarding_completed_at = utcnow()
        session.commit()
    return _safe_user(user)


@router.post("/logout", status_code=204)
def logout(
    request: Request,
    response: Response,
    session: Annotated[Session, Depends(_session)],
) -> Response:
    auth_session = current_session(request, session, require_user=True)
    validate_csrf(request, auth_session, _settings(request))
    session.delete(auth_session)
    session.commit()
    clear_session_cookies(response, _settings(request))
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.post("/verify-email", response_model=AuthActionRead)
def verify_email(
    payload: VerifyEmailRequest,
    request: Request,
    session: Annotated[Session, Depends(_session)],
) -> AuthActionRead:
    preauth = _preauth_session(request, session, _settings(request))
    del preauth
    challenge = session.get(AuthChallenge, token_hash(payload.token))
    if challenge is None or challenge.kind != "email_verification" or challenge.consumed_at is not None or is_expired(challenge.expires_at):
        raise HTTPException(status_code=400, detail="This verification link is invalid or expired.")
    user = session.get(User, challenge.user_id) if challenge.user_id else None
    if user is None:
        raise HTTPException(status_code=400, detail="This verification link is invalid or expired.")
    challenge.consumed_at = utcnow()
    user.password_hash = hash_password(payload.password)
    user.email_verified = True
    claim_legacy_workspace(session, user.id)
    session.commit()
    return AuthActionRead(message="Email verified. You can now sign in.")


@router.post("/forgot-password", response_model=AuthActionRead, status_code=202)
def forgot_password(
    payload: EmailRequest,
    request: Request,
    session: Annotated[Session, Depends(_session)],
) -> AuthActionRead:
    _preauth_session(request, session, _settings(request))
    check_rate_limit("recovery:" + (request.client.host if request.client else "unknown"), limit=8)
    settings = _settings(request)
    if not settings.email_delivery_configured:
        raise HTTPException(status_code=503, detail="Password recovery is unavailable because email delivery is not configured.")
    user = session.scalar(select(User).where(User.email == payload.email))
    if user:
        token = secrets.token_urlsafe(40)
        create_challenge(session, kind="password_reset", user_id=user.id, token=token, settings=settings,
                         ttl=timedelta(minutes=30))
        session.flush()
        try:
            send_email(settings, user.email, "Reset your SID password", (
                "Use the link below to choose a new password. The link expires in 30 minutes.\n\n"
                + _email_link(settings, "/reset-password", token)
                + "\n\nIf you did not request this, you can ignore this message."
            ))
            session.commit()
        except Exception:
            session.rollback()
    return AuthActionRead(message=_GENERIC_RECOVERY_MESSAGE)


@router.post("/reset-password", response_model=AuthActionRead)
def reset_password(
    payload: ResetPasswordRequest,
    request: Request,
    session: Annotated[Session, Depends(_session)],
) -> AuthActionRead:
    _preauth_session(request, session, _settings(request))
    challenge = session.get(AuthChallenge, token_hash(payload.token))
    now = utcnow()
    if challenge is None or challenge.kind != "password_reset" or challenge.consumed_at is not None or is_expired(challenge.expires_at):
        raise HTTPException(status_code=400, detail="This password reset link is invalid or expired.")
    user = session.get(User, challenge.user_id) if challenge.user_id else None
    if user is None:
        raise HTTPException(status_code=400, detail="This password reset link is invalid or expired.")
    user.password_hash = hash_password(payload.password)
    challenge.consumed_at = now
    session.execute(update(AuthSession).where(AuthSession.user_id == user.id).values(expires_at=now))
    session.commit()
    return AuthActionRead(message="Password updated. Sign in with your new password.")


