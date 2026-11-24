from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.v1.health import get_db_session
from app.models.exploration import Exploration
from app.models.project import Project, ProjectExploration
from app.models.workflow import Workflow
from app.schemas.workflow import (
    ProjectWorkflowRebuildRead,
    WorkflowPageRead,
    WorkflowRead,
    WorkflowRebuildRead,
    WorkflowStatusFilter,
    WorkflowUpdate,
)
from app.services.projects import ArchivedProjectError, ProjectNotFound, assert_project_active
from app.services.workflows import (
    WorkflowNotFound,
    WorkflowRebuildUnavailable,
    WorkflowSourceNotFound,
    get_workflow,
    list_exploration_workflows,
    list_project_workflows,
    mark_workflow_reviewed,
    rebuild_exploration_workflows,
    update_workflow,
)


router = APIRouter(tags=["workflow intelligence"])


def _exploration_or_404(session: Session, exploration_id: UUID) -> Exploration:
    exploration = session.get(Exploration, exploration_id)
    if exploration is None:
        raise HTTPException(status_code=404, detail={"code": "exploration_not_found", "message": "Exploration was not found."})
    return exploration


def _project_or_404(session: Session, project_id: UUID) -> Project:
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail={"code": "project_not_found", "message": "Project was not found."})
    return project


def _workflow_or_404(session: Session, exploration_id: UUID, workflow_id: UUID) -> Workflow:
    try:
        return get_workflow(session, exploration_id, workflow_id)
    except WorkflowNotFound as exc:
        raise HTTPException(status_code=404, detail={"code": "workflow_not_found", "message": str(exc)}) from exc


def _project_workflow_or_404(session: Session, project_id: UUID, workflow_id: UUID) -> Workflow:
    workflow = session.scalar(
        select(Workflow)
        .join(ProjectExploration, ProjectExploration.exploration_id == Workflow.exploration_id)
        .where(ProjectExploration.project_id == project_id, Workflow.id == workflow_id)
    )
    if workflow is None:
        raise HTTPException(status_code=404, detail={"code": "workflow_not_found", "message": "Workflow was not found for this project."})
    return get_workflow(session, workflow.exploration_id, workflow.id)


def _translate_rebuild_error(exc: WorkflowRebuildUnavailable) -> HTTPException:
    archived = "archived" in str(exc).casefold()
    return HTTPException(
        status_code=409,
        detail={
            "code": "project_archived" if archived else "workflow_rebuild_unavailable",
            "message": str(exc),
        },
    )


@router.get("/explorations/{exploration_id}/workflows", response_model=WorkflowPageRead)
def get_exploration_workflows(
    exploration_id: UUID,
    session: Annotated[Session, Depends(get_db_session)],
    search: Annotated[str | None, Query(max_length=200)] = None,
    provenance: Literal["observed", "suggested", "user_authored"] | None = None,
    workflow_status: WorkflowStatusFilter | None = Query(default=None, alias="status"),
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    _exploration_or_404(session, exploration_id)
    items, total = list_exploration_workflows(
        session, exploration_id, search=search, provenance=provenance,
        status=workflow_status, limit=limit, offset=offset,
    )
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.post(
    "/explorations/{exploration_id}/workflows/rebuild",
    response_model=WorkflowRebuildRead,
)
def rebuild_workflows(
    exploration_id: UUID,
    session: Annotated[Session, Depends(get_db_session)],
) -> dict:
    try:
        result = rebuild_exploration_workflows(session, exploration_id)
        session.commit()
        return result
    except WorkflowSourceNotFound as exc:
        session.rollback()
        raise HTTPException(status_code=404, detail={"code": "exploration_not_found", "message": str(exc)}) from exc
    except WorkflowRebuildUnavailable as exc:
        session.rollback()
        raise _translate_rebuild_error(exc) from exc
    except Exception:
        session.rollback()
        raise


@router.get("/explorations/{exploration_id}/workflows/{workflow_id}", response_model=WorkflowRead)
def get_exploration_workflow(
    exploration_id: UUID,
    workflow_id: UUID,
    session: Annotated[Session, Depends(get_db_session)],
) -> Workflow:
    _exploration_or_404(session, exploration_id)
    return _workflow_or_404(session, exploration_id, workflow_id)


@router.patch("/explorations/{exploration_id}/workflows/{workflow_id}", response_model=WorkflowRead)
def patch_exploration_workflow(
    exploration_id: UUID,
    workflow_id: UUID,
    request_data: WorkflowUpdate,
    session: Annotated[Session, Depends(get_db_session)],
) -> Workflow:
    _exploration_or_404(session, exploration_id)
    workflow = _workflow_or_404(session, exploration_id, workflow_id)
    update_workflow(workflow, name=request_data.name, description=request_data.description)
    session.commit()
    session.refresh(workflow)
    return get_workflow(session, exploration_id, workflow_id)


@router.post("/explorations/{exploration_id}/workflows/{workflow_id}/review", response_model=WorkflowRead)
def review_exploration_workflow(
    exploration_id: UUID,
    workflow_id: UUID,
    session: Annotated[Session, Depends(get_db_session)],
) -> Workflow:
    _exploration_or_404(session, exploration_id)
    workflow = _workflow_or_404(session, exploration_id, workflow_id)
    mark_workflow_reviewed(workflow)
    session.commit()
    return get_workflow(session, exploration_id, workflow_id)


@router.get("/projects/{project_id}/workflows", response_model=WorkflowPageRead)
def get_project_workflows(
    project_id: UUID,
    session: Annotated[Session, Depends(get_db_session)],
    search: Annotated[str | None, Query(max_length=200)] = None,
    provenance: Literal["observed", "suggested", "user_authored"] | None = None,
    workflow_status: WorkflowStatusFilter | None = Query(default=None, alias="status"),
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    _project_or_404(session, project_id)
    items, total = list_project_workflows(
        session, project_id, search=search, provenance=provenance,
        status=workflow_status, limit=limit, offset=offset,
    )
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.post("/projects/{project_id}/workflows/rebuild", response_model=ProjectWorkflowRebuildRead)
def rebuild_project_workflows(
    project_id: UUID,
    session: Annotated[Session, Depends(get_db_session)],
) -> dict:
    try:
        project = assert_project_active(session, project_id)
    except ArchivedProjectError as exc:
        raise _translate_rebuild_error(WorkflowRebuildUnavailable(str(exc))) from exc
    except ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail={"code": "project_not_found", "message": "Project was not found."}) from exc
    exploration_ids = list(session.scalars(
        select(ProjectExploration.exploration_id)
        .join(Exploration, Exploration.id == ProjectExploration.exploration_id)
        .where(ProjectExploration.project_id == project.id, Exploration.status == "completed")
        .order_by(ProjectExploration.exploration_id)
    ))
    incomplete_count = session.scalar(
        select(func.count())
        .select_from(ProjectExploration)
        .join(Exploration, Exploration.id == ProjectExploration.exploration_id)
        .where(ProjectExploration.project_id == project.id, Exploration.status != "completed")
    ) or 0
    totals = {"created": 0, "updated": 0, "unchanged": 0, "marked_stale": 0, "preserved_manual": 0, "skipped_invalid_sources": 0}
    try:
        for exploration_id in exploration_ids:
            result = rebuild_exploration_workflows(session, exploration_id)
            for key in totals:
                totals[key] += int(result[key])
        session.commit()
    except WorkflowRebuildUnavailable as exc:
        session.rollback()
        raise _translate_rebuild_error(exc) from exc
    except Exception:
        session.rollback()
        raise
    return {
        "project_id": project_id,
        "rebuilt_explorations": len(exploration_ids),
        "skipped_incomplete_explorations": int(incomplete_count),
        **totals,
    }


@router.get("/projects/{project_id}/workflows/{workflow_id}", response_model=WorkflowRead)
def get_project_workflow(
    project_id: UUID,
    workflow_id: UUID,
    session: Annotated[Session, Depends(get_db_session)],
) -> Workflow:
    _project_or_404(session, project_id)
    return _project_workflow_or_404(session, project_id, workflow_id)


@router.patch("/projects/{project_id}/workflows/{workflow_id}", response_model=WorkflowRead)
def patch_project_workflow(
    project_id: UUID,
    workflow_id: UUID,
    request_data: WorkflowUpdate,
    session: Annotated[Session, Depends(get_db_session)],
) -> Workflow:
    _project_or_404(session, project_id)
    workflow = _project_workflow_or_404(session, project_id, workflow_id)
    update_workflow(workflow, name=request_data.name, description=request_data.description)
    session.commit()
    return get_workflow(session, workflow.exploration_id, workflow.id)


@router.post("/projects/{project_id}/workflows/{workflow_id}/review", response_model=WorkflowRead)
def review_project_workflow(
    project_id: UUID,
    workflow_id: UUID,
    session: Annotated[Session, Depends(get_db_session)],
) -> Workflow:
    _project_or_404(session, project_id)
    workflow = _project_workflow_or_404(session, project_id, workflow_id)
    mark_workflow_reviewed(workflow)
    session.commit()
    return get_workflow(session, workflow.exploration_id, workflow.id)

