from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.v1.health import get_db_session
from app.core.config import get_settings
from app.core.url_security import UnsafeTargetUrl, assert_public_http_url
from app.models.exploration import Exploration, ExplorationEvent, ExplorationPage
from app.schemas.exploration import (
    ExplorationCreate,
    ExplorationEventRead,
    ExplorationPageRead,
    ExplorationRead,
)
from app.services.exploration_worker import ExplorationManager, append_event

router = APIRouter(prefix="/explorations", tags=["explorations"])


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"code": "exploration_not_found", "message": "Exploration was not found."},
    )


def _get_exploration(session: Session, exploration_id: UUID) -> Exploration:
    exploration = session.get(Exploration, exploration_id)
    if exploration is None:
        raise _not_found()
    return exploration


@router.post("", response_model=ExplorationRead, status_code=202)
async def create_exploration(
    request_data: ExplorationCreate,
    request: Request,
    session: Annotated[Session, Depends(get_db_session)],
) -> Exploration:
    settings = get_settings()
    try:
        await assert_public_http_url(request_data.url)
    except UnsafeTargetUrl as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "unsafe_target_url", "message": str(exc)},
        ) from exc

    manager: ExplorationManager | None = getattr(request.app.state, "exploration_manager", None)
    if manager is None:
        raise HTTPException(
            status_code=503,
            detail={"code": "exploration_worker_unavailable", "message": "The exploration worker is unavailable."},
        )
    exploration = Exploration(
        start_url=request_data.url,
        status="queued",
        max_pages=settings.exploration_max_pages,
        max_depth=settings.exploration_max_depth,
        pages_discovered=0,
        elements_discovered=0,
        event_count=0,
    )
    session.add(exploration)
    session.flush()
    append_event(session, exploration, "exploration_queued", "Exploration was accepted and queued.", request_data.url)
    session.commit()
    session.refresh(exploration)
    manager.enqueue(exploration.id)
    return exploration


@router.get("", response_model=list[ExplorationRead])
def list_explorations(
    session: Annotated[Session, Depends(get_db_session)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Exploration]:
    return list(
        session.scalars(
            select(Exploration)
            .order_by(Exploration.created_at.desc(), Exploration.id)
            .offset(offset)
            .limit(limit)
        )
    )


@router.get("/{exploration_id}", response_model=ExplorationRead)
def get_exploration(
    exploration_id: UUID,
    session: Annotated[Session, Depends(get_db_session)],
) -> Exploration:
    return _get_exploration(session, exploration_id)


@router.post("/{exploration_id}/cancel", response_model=ExplorationRead)
async def cancel_exploration(
    exploration_id: UUID,
    request: Request,
    session: Annotated[Session, Depends(get_db_session)],
) -> Exploration:
    exploration = _get_exploration(session, exploration_id)
    if exploration.status not in {"queued", "running"}:
        raise HTTPException(
            status_code=409,
            detail={"code": "exploration_not_active", "message": "Only queued or running explorations can be cancelled."},
        )

    manager: ExplorationManager | None = getattr(request.app.state, "exploration_manager", None)
    if manager is not None:
        await manager.cancel(exploration_id)
    session.expire_all()
    exploration = _get_exploration(session, exploration_id)
    if exploration.status in {"queued", "running"}:
        exploration.status = "cancelled"
        exploration.completed_at = datetime.now(UTC)
        append_event(
            session,
            exploration,
            "exploration_cancelled",
            "Exploration was cancelled before a worker began.",
            exploration.current_url,
        )
        session.commit()
        session.refresh(exploration)
    return exploration


@router.get("/{exploration_id}/pages", response_model=list[ExplorationPageRead])
def list_exploration_pages(
    exploration_id: UUID,
    session: Annotated[Session, Depends(get_db_session)],
) -> list[ExplorationPageRead]:
    _get_exploration(session, exploration_id)
    pages = session.scalars(
        select(ExplorationPage)
        .options(selectinload(ExplorationPage.elements))
        .where(ExplorationPage.exploration_id == exploration_id)
        .order_by(ExplorationPage.depth, ExplorationPage.discovered_at, ExplorationPage.url)
    )
    return [
        ExplorationPageRead.model_validate(page).model_copy(
            update={
                "screenshot_url": (
                    f"/api/v1/explorations/{exploration_id}/pages/{page.id}/screenshot"
                    if page.screenshot
                    else None
                )
            }
        )
        for page in pages
    ]


@router.get("/{exploration_id}/pages/{page_id}/screenshot")
def get_page_screenshot(
    exploration_id: UUID,
    page_id: UUID,
    session: Annotated[Session, Depends(get_db_session)],
) -> Response:
    page = session.scalar(
        select(ExplorationPage).where(
            ExplorationPage.id == page_id,
            ExplorationPage.exploration_id == exploration_id,
        )
    )
    if page is None:
        raise _not_found()
    if not page.screenshot:
        raise HTTPException(
            status_code=404,
            detail={"code": "screenshot_not_found", "message": "This page has no saved screenshot."},
        )
    return Response(
        content=page.screenshot,
        media_type=page.screenshot_mime or "image/jpeg",
        headers={"Cache-Control": "private, max-age=300"},
    )


@router.get("/{exploration_id}/events", response_model=list[ExplorationEventRead])
def list_exploration_events(
    exploration_id: UUID,
    session: Annotated[Session, Depends(get_db_session)],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    after: Annotated[int, Query(ge=0)] = 0,
) -> list[ExplorationEvent]:
    _get_exploration(session, exploration_id)
    return list(
        session.scalars(
            select(ExplorationEvent)
            .where(
                ExplorationEvent.exploration_id == exploration_id,
                ExplorationEvent.sequence > after,
            )
            .order_by(ExplorationEvent.sequence)
            .limit(limit)
        )
    )
