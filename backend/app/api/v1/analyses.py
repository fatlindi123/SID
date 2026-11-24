from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.v1.health import get_db_session
from app.models.exploration import Exploration, ExplorationAnalysis, ExplorationPage
from app.schemas.analysis import AnalysisResult, AnalysisResultsRead, AnalysisStatusRead
from app.services.analysis_worker import AnalysisManager

router = APIRouter(prefix="/explorations", tags=["AI analysis"])


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


def _status_read(exploration_id: UUID, analysis: ExplorationAnalysis | None) -> AnalysisStatusRead:
    if analysis is None:
        return AnalysisStatusRead(exploration_id=exploration_id, status="not_started")
    return AnalysisStatusRead.model_validate(analysis)


@router.post(
    "/{exploration_id}/analysis",
    response_model=AnalysisStatusRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_analysis(
    exploration_id: UUID,
    request: Request,
    session: Annotated[Session, Depends(get_db_session)],
) -> AnalysisStatusRead:
    exploration = _get_exploration(session, exploration_id)
    if exploration.status != "completed":
        raise HTTPException(
            status_code=409,
            detail={"code": "exploration_not_completed", "message": "AI analysis requires a completed exploration."},
        )
    saved_page_count = session.scalar(
        select(func.count()).select_from(ExplorationPage).where(
            ExplorationPage.exploration_id == exploration_id
        )
    )
    if not saved_page_count:
        raise HTTPException(
            status_code=409,
            detail={"code": "exploration_has_no_pages", "message": "This exploration has no saved pages to analyze."},
        )
    manager: AnalysisManager | None = getattr(request.app.state, "analysis_manager", None)
    if manager is None:
        raise HTTPException(
            status_code=503,
            detail={"code": "analysis_worker_unavailable", "message": "The AI analysis worker is unavailable."},
        )

    analysis = session.scalar(
        select(ExplorationAnalysis)
        .where(ExplorationAnalysis.exploration_id == exploration_id)
        .with_for_update()
    )
    if analysis is not None and analysis.status in {"queued", "running"}:
        manager.enqueue(exploration_id)
        return _status_read(exploration_id, analysis)

    now = datetime.now(UTC)
    if analysis is None:
        analysis = ExplorationAnalysis(
            exploration_id=exploration_id,
            status="queued",
            attempt_count=1,
            requested_at=now,
        )
        session.add(analysis)
    else:
        analysis.status = "queued"
        analysis.attempt_count += 1
        analysis.result = None
        analysis.error_message = None
        analysis.requested_at = now
        analysis.started_at = None
        analysis.completed_at = None
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        analysis = session.scalar(
            select(ExplorationAnalysis).where(
                ExplorationAnalysis.exploration_id == exploration_id
            )
        )
        if analysis is None:
            raise
        manager.enqueue(exploration_id)
        return _status_read(exploration_id, analysis)

    session.refresh(analysis)
    manager.enqueue(exploration_id)
    return _status_read(exploration_id, analysis)


@router.get("/{exploration_id}/analysis", response_model=AnalysisStatusRead)
def get_analysis_status(
    exploration_id: UUID,
    session: Annotated[Session, Depends(get_db_session)],
) -> AnalysisStatusRead:
    _get_exploration(session, exploration_id)
    analysis = session.scalar(
        select(ExplorationAnalysis).where(
            ExplorationAnalysis.exploration_id == exploration_id
        )
    )
    return _status_read(exploration_id, analysis)


@router.get("/{exploration_id}/analysis/results", response_model=AnalysisResultsRead)
def get_analysis_results(
    exploration_id: UUID,
    session: Annotated[Session, Depends(get_db_session)],
) -> AnalysisResultsRead:
    _get_exploration(session, exploration_id)
    analysis = session.scalar(
        select(ExplorationAnalysis).where(
            ExplorationAnalysis.exploration_id == exploration_id
        )
    )
    if analysis is None or analysis.status != "completed" or analysis.result is None:
        code = "analysis_not_started" if analysis is None else "analysis_not_completed"
        message = (
            "AI analysis has not been started."
            if analysis is None
            else analysis.error_message or "AI analysis results are not available yet."
        )
        raise HTTPException(status_code=409, detail={"code": code, "message": message})
    result = AnalysisResult.model_validate(analysis.result)
    return AnalysisResultsRead(
        id=analysis.id,
        exploration_id=analysis.exploration_id,
        model=analysis.model,
        completed_at=analysis.completed_at,
        result=result,
    )
