from collections.abc import Generator
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.auth import User
from app.services.auth import current_session, validate_csrf
from app.schemas.health import HealthResponse
from app.services.health import check_database

router = APIRouter()


def get_public_db_session(request: Request) -> Generator[Session, None, None]:
    factory = getattr(request.app.state, "test_session_factory", SessionLocal)
    with factory() as session:
        yield session


@router.get("/health", response_model=HealthResponse, summary="Check API and database health")
def get_health(
    session: Annotated[Session, Depends(get_public_db_session)],
) -> HealthResponse:
    check_database(session)
    return HealthResponse(status="ok", database="connected")


def get_db_session(request: Request) -> Generator[Session, None, None]:
    """Authenticated, CSRF-checked workspace database session."""
    factory = getattr(request.app.state, "test_session_factory", SessionLocal)
    with factory() as session:
        auth_session = current_session(request, session, require_user=True)
        if request.method.upper() in {"POST", "PUT", "PATCH", "DELETE"}:
            validate_csrf(request, auth_session, request.app.state.settings)
        user: User = request.state.user
        session.info["owner_id"] = user.id
        yield session
