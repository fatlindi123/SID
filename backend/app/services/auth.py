from __future__ import annotations

import hmac
import secrets
import threading
from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import HTTPException, Request, Response, status
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from app.core.auth_security import csrf_origin_allowed, hash_password, token_hash
from app.core.config import Settings
from app.models.auth import AuthBootstrap, AuthChallenge, AuthSession, User
from app.models.ownership import WorkspaceRecordOwner

SESSION_COOKIE = "inscope_session"
_rate_lock = threading.Lock()
_auth_attempts: dict[str, deque[datetime]] = defaultdict(deque)


def utcnow() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def is_expired(value: datetime) -> bool:
    return _aware(value) <= utcnow()


def current_session(request: Request, session: Session, *, require_user: bool) -> AuthSession:
    raw = request.cookies.get(SESSION_COOKIE)
    record = session.get(AuthSession, token_hash(raw)) if raw else None
    if record is None or _aware(record.expires_at) <= utcnow():
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication is required.")
    if require_user:
        if record.user_id is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication is required.")
        user = session.get(User, record.user_id)
        if user is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication is required.")
        request.state.user = user
        session.info["owner_id"] = user.id
    return record


def validate_csrf(request: Request, auth_session: AuthSession, settings: Settings) -> None:
    supplied = request.headers.get("x-csrf-token")
    if not csrf_origin_allowed(request.headers.get("origin"), settings):
        raise HTTPException(status_code=403, detail="The request origin could not be verified.")
    if not supplied or not hmac.compare_digest(token_hash(supplied), auth_session.csrf_hash):
        raise HTTPException(status_code=403, detail="The security token is missing or expired. Refresh the page and try again.")


def ensure_csrf_session(request: Request, response: Response, session: Session, settings: Settings) -> str:
    raw = request.cookies.get(SESSION_COOKIE)
    record = session.get(AuthSession, token_hash(raw)) if raw else None
    now = utcnow()
    if record is None or _aware(record.expires_at) <= now:
        raw = secrets.token_urlsafe(48)
        csrf = secrets.token_urlsafe(32)
        record = AuthSession(
            token_hash=token_hash(raw),
            csrf_hash=token_hash(csrf),
            expires_at=now + timedelta(minutes=30),
        )
        session.add(record)
        _set_session_cookie(response, raw, settings, max_age=1800)
    else:
        csrf = secrets.token_urlsafe(32)
        record.csrf_hash = token_hash(csrf)
    session.commit()
    return csrf


def issue_authenticated_session(
    request: Request,
    response: Response,
    session: Session,
    settings: Settings,
    user_id: UUID,
) -> str:
    old_raw = request.cookies.get(SESSION_COOKIE)
    if old_raw:
        old = session.get(AuthSession, token_hash(old_raw))
        if old:
            session.delete(old)
    raw = secrets.token_urlsafe(48)
    csrf = secrets.token_urlsafe(32)
    session.add(AuthSession(
        token_hash=token_hash(raw),
        csrf_hash=token_hash(csrf),
        user_id=user_id,
        expires_at=utcnow() + timedelta(hours=settings.auth_session_ttl_hours),
    ))
    _set_session_cookie(response, raw, settings, max_age=settings.auth_session_ttl_hours * 3600)
    return csrf


def clear_session_cookies(response: Response, settings: Settings) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/", secure=settings.app_environment == "production", httponly=True, samesite="lax")


def _set_session_cookie(response: Response, raw: str, settings: Settings, *, max_age: int) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        raw,
        max_age=max_age,
        path="/",
        secure=settings.app_environment == "production",
        httponly=True,
        samesite="lax",
    )


def create_challenge(
    session: Session,
    *,
    kind: str,
    user_id: UUID | None,
    token: str,
    settings: Settings,
    nonce: str | None = None,
    redirect_to: str | None = None,
    ttl: timedelta | None = None,
) -> AuthChallenge:
    now = utcnow()
    row = AuthChallenge(
        token_hash=token_hash(token),
        kind=kind,
        user_id=user_id,
        nonce_hash=token_hash(nonce) if nonce else None,
        redirect_to=redirect_to,
        expires_at=now + (ttl or timedelta(minutes=settings.auth_challenge_ttl_minutes)),
    )
    session.add(row)
    return row


def claim_legacy_workspace(session: Session, user_id: UUID) -> None:
    bootstrap = session.scalar(select(AuthBootstrap).where(AuthBootstrap.id == 1).with_for_update())
    if bootstrap is None or bootstrap.claimed_by is not None:
        return
    from app.models.exploration import Exploration, ExplorationAnalysis, ExplorationEvent, ExplorationPage, ObservedElement
    from app.models.interaction_observation import InteractionObservation
    from app.models.knowledge_graph import KnowledgeGraphEdge, KnowledgeGraphNode
    from app.models.project import Project, ProjectExploration, ProjectMetadata
    from app.models.report import GeneratedReport
    from app.models.test_case import TestCase
    from app.models.test_execution import TestRun, TestRunStep
    from app.models.workflow import Workflow, WorkflowStep

    for model in (
        Exploration, ExplorationAnalysis, ExplorationEvent, ExplorationPage, ObservedElement,
        InteractionObservation, KnowledgeGraphEdge, KnowledgeGraphNode, Project, ProjectExploration,
        ProjectMetadata, GeneratedReport, TestCase, TestRun, TestRunStep, Workflow, WorkflowStep,
    ):
        primary_key = inspect(model).primary_key
        for row in session.execute(select(*primary_key)):
            session.add(WorkspaceRecordOwner(
                resource_type=model.__tablename__,
                resource_id=row[0],
                owner_id=user_id,
            ))
    bootstrap.claimed_by = user_id
    bootstrap.claimed_at = utcnow()


def check_rate_limit(key: str, *, limit: int = 12, window_seconds: int = 900) -> None:
    now = utcnow()
    with _rate_lock:
        attempts = _auth_attempts[key]
        cutoff = now - timedelta(seconds=window_seconds)
        while attempts and attempts[0] <= cutoff:
            attempts.popleft()
        if len(attempts) >= limit:
            raise HTTPException(status_code=429, detail="Too many attempts. Wait a while and try again.")
        attempts.append(now)


