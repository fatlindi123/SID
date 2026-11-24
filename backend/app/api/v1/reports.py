import logging
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from app.api.v1.health import get_db_session
from app.models.project import Project
from app.models.report import GeneratedReport
from app.schemas.report import ReportGenerationRequest, ReportPageRead, ReportRead
from app.services.reports import (
    ReportGenerationFailed,
    ReportNotFound,
    ReportNotReady,
    ReportSourceNotFound,
    generate_exploration_report,
    generate_project_report,
    get_report,
    list_reports,
    render_report_html,
    render_report_pdf,
    require_completed_report,
)

logger = logging.getLogger("software_intelligence.reports.api")
router = APIRouter(prefix="/reports", tags=["reports"])


def _report_or_404(session: Session, report_id: UUID) -> GeneratedReport:
    try:
        return get_report(session, report_id)
    except ReportNotFound as exc:
        raise HTTPException(status_code=404, detail={"code": "report_not_found", "message": "Report was not found."}) from exc


def _generate_failure(exc: ReportGenerationFailed) -> HTTPException:
    return HTTPException(
        status_code=500,
        detail={"code": "report_generation_failed", "message": "The report could not be generated. The failed report record is available in report history."},
    )


def _filters(request_data: ReportGenerationRequest) -> dict:
    return request_data.model_dump(mode="json", exclude_none=True)


@router.post("/explorations/{exploration_id}", response_model=ReportRead, status_code=201)
def create_exploration_report(
    exploration_id: UUID,
    session: Annotated[Session, Depends(get_db_session)],
    request_data: ReportGenerationRequest = ReportGenerationRequest(),
) -> GeneratedReport:
    try:
        return generate_exploration_report(session, exploration_id, _filters(request_data))
    except ReportSourceNotFound as exc:
        raise HTTPException(status_code=404, detail={"code": "exploration_not_found", "message": "Exploration was not found."}) from exc
    except ReportGenerationFailed as exc:
        raise _generate_failure(exc) from exc


@router.post("/projects/{project_id}", response_model=ReportRead, status_code=201)
def create_project_report(
    project_id: UUID,
    session: Annotated[Session, Depends(get_db_session)],
    request_data: ReportGenerationRequest = ReportGenerationRequest(),
) -> GeneratedReport:
    try:
        return generate_project_report(session, project_id, _filters(request_data))
    except ReportSourceNotFound as exc:
        raise HTTPException(status_code=404, detail={"code": "project_not_found", "message": "Project was not found."}) from exc
    except ReportGenerationFailed as exc:
        raise _generate_failure(exc) from exc


@router.get("", response_model=ReportPageRead)
def report_history(
    session: Annotated[Session, Depends(get_db_session)],
    report_type: Literal["exploration", "project"] | None = None,
    project_id: UUID | None = None,
    exploration_id: UUID | None = None,
    generated_from: datetime | None = None,
    generated_to: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    if generated_from is not None and generated_from.tzinfo is None:
        raise HTTPException(status_code=422, detail={"code": "timezone_required", "message": "generated_from must include a timezone."})
    if generated_to is not None and generated_to.tzinfo is None:
        raise HTTPException(status_code=422, detail={"code": "timezone_required", "message": "generated_to must include a timezone."})
    if generated_from is not None and generated_to is not None and generated_from > generated_to:
        raise HTTPException(status_code=422, detail={"code": "invalid_date_range", "message": "generated_from must be earlier than or equal to generated_to."})
    try:
        rows, total = list_reports(
            session, report_type=report_type, project_id=project_id, exploration_id=exploration_id,
            generated_from=generated_from, generated_to=generated_to, limit=limit, offset=offset,
        )
    except ReportSourceNotFound as exc:
        code = "project_not_found" if project_id is not None and session.get(Project, project_id) is None else "exploration_not_found"
        raise HTTPException(status_code=404, detail={"code": code, "message": str(exc)}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": "invalid_report_scope", "message": str(exc)}) from exc
    return {"items": rows, "total": total, "limit": limit, "offset": offset}


@router.get("/{report_id}", response_model=ReportRead)
def report_detail(report_id: UUID, session: Annotated[Session, Depends(get_db_session)]) -> GeneratedReport:
    return _report_or_404(session, report_id)


def _completed_report(session: Session, report_id: UUID) -> tuple[GeneratedReport, dict]:
    report = _report_or_404(session, report_id)
    try:
        return report, require_completed_report(report)
    except ReportNotReady as exc:
        raise HTTPException(status_code=409, detail={"code": "report_not_completed", "message": "This saved report has no completed snapshot to export."}) from exc


@router.get("/{report_id}/export.json")
def report_json_export(report_id: UUID, session: Annotated[Session, Depends(get_db_session)]) -> JSONResponse:
    _report, saved = _completed_report(session, report_id)
    return JSONResponse(
        content=saved,
        headers={"Content-Disposition": f'attachment; filename="software-intelligence-report-{report_id}.json"'},
    )


@router.get("/{report_id}/print", response_class=HTMLResponse)
def report_print_view(report_id: UUID, session: Annotated[Session, Depends(get_db_session)]) -> HTMLResponse:
    _report, saved = _completed_report(session, report_id)
    return HTMLResponse(
        content=render_report_html(saved),
        headers={
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
        },
    )


@router.get("/{report_id}/export.pdf")
async def report_pdf_export(report_id: UUID, session: Annotated[Session, Depends(get_db_session)]) -> Response:
    _report, saved = _completed_report(session, report_id)
    try:
        pdf = await render_report_pdf(saved)
    except Exception as exc:
        logger.error(
            "report_pdf_export_failed",
            extra={"report_id": str(report_id), "exception_type": type(exc).__name__},
        )
        raise HTTPException(
            status_code=503,
            detail={"code": "report_pdf_unavailable", "message": "PDF export is temporarily unavailable."},
        ) from None
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="software-intelligence-report-{report_id}.pdf"'},
    )
