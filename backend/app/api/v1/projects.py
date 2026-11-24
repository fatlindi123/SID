from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.v1.health import get_db_session
from app.models.exploration import Exploration
from app.models.knowledge_graph import KnowledgeGraphEdge, KnowledgeGraphNode
from app.models.project import Project, ProjectExploration, ProjectMetadata
from app.models.test_case import TestCase
from app.models.test_execution import TestRun, TestRunStep
from app.services.workflows import sync_workflow_project_association
from app.schemas.project import (
    ProjectCreate,
    ProjectExplorationAssociationRead,
    ProjectExplorationPageRead,
    ProjectExplorationSummaryRead,
    ProjectGraphAvailabilityRead,
    ProjectGraphPageRead,
    ProjectPageRead,
    ProjectRead,
    ProjectRunPageRead,
    ProjectRunSummaryRead,
    ProjectTestCasePageRead,
    ProjectTestCaseSummaryRead,
    ProjectUpdate,
)
from app.services.projects import (
    ArchivedProjectError,
    ProjectAssociationConflict,
    ProjectNotFound,
    archive_project,
    assert_project_active,
    get_project,
    get_project_metadata,
    project_response,
    project_stats,
    restore_project,
)

router = APIRouter(prefix="/projects", tags=["projects"])


def _project_not_found() -> HTTPException:
    return HTTPException(status_code=404, detail={"code": "project_not_found", "message": "Project was not found."})


def _read_project(session: Session, project_id: UUID) -> dict:
    try:
        project = get_project(session, project_id)
    except ProjectNotFound as exc:
        raise _project_not_found() from exc
    metadata = get_project_metadata(session, project.id)
    stats = project_stats(session, [project.id])[project.id]
    return project_response(project, metadata, stats)


def _exploration_counts(session: Session, exploration_ids: list[UUID]) -> dict[UUID, dict[str, int | bool]]:
    counts = {item: {"test_cases": 0, "test_runs": 0, "nodes": 0, "edges": 0} for item in exploration_ids}
    if not exploration_ids:
        return counts
    rows = session.execute(
        select(TestCase.exploration_id, func.count(TestCase.id))
        .where(
            TestCase.exploration_id.in_(exploration_ids),
            TestCase.is_current.is_(True),
            TestCase.deleted_at.is_(None),
        )
        .group_by(TestCase.exploration_id)
    ).all()
    for exploration_id, count in rows:
        counts[exploration_id]["test_cases"] = int(count or 0)
    rows = session.execute(
        select(TestRun.exploration_id, func.count(TestRun.id))
        .where(TestRun.exploration_id.in_(exploration_ids))
        .group_by(TestRun.exploration_id)
    ).all()
    for exploration_id, count in rows:
        counts[exploration_id]["test_runs"] = int(count or 0)
    rows = session.execute(
        select(KnowledgeGraphNode.exploration_id, func.count(KnowledgeGraphNode.id))
        .where(KnowledgeGraphNode.exploration_id.in_(exploration_ids))
        .group_by(KnowledgeGraphNode.exploration_id)
    ).all()
    for exploration_id, count in rows:
        counts[exploration_id]["nodes"] = int(count or 0)
    rows = session.execute(
        select(KnowledgeGraphEdge.exploration_id, func.count(KnowledgeGraphEdge.id))
        .where(KnowledgeGraphEdge.exploration_id.in_(exploration_ids))
        .group_by(KnowledgeGraphEdge.exploration_id)
    ).all()
    for exploration_id, count in rows:
        counts[exploration_id]["edges"] = int(count or 0)
    return counts


@router.get("/available-explorations", response_model=ProjectExplorationPageRead)
def list_available_explorations(
    session: Annotated[Session, Depends(get_db_session)],
    search: Annotated[str | None, Query(max_length=300)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    statement = select(Exploration).where(
        ~select(ProjectExploration.exploration_id)
        .where(ProjectExploration.exploration_id == Exploration.id)
        .exists()
    )
    if search and search.strip():
        statement = statement.where(Exploration.start_url.ilike(f"%{search.strip()}%"))
    total = session.scalar(select(func.count()).select_from(statement.subquery())) or 0
    items = list(
        session.scalars(
            statement.order_by(Exploration.created_at.desc(), Exploration.id)
            .offset(offset)
            .limit(limit)
        )
    )
    counts = _exploration_counts(session, [item.id for item in items])
    return {
        "items": [
            {
                "id": item.id,
                "start_url": item.start_url,
                "status": item.status,
                "pages_discovered": item.pages_discovered,
                "elements_discovered": item.elements_discovered,
                "event_count": item.event_count,
                "created_at": item.created_at,
                "completed_at": item.completed_at,
                "test_case_count": counts[item.id]["test_cases"],
                "test_run_count": counts[item.id]["test_runs"],
                "knowledge_graph_available": bool(counts[item.id]["nodes"]),
            }
            for item in items
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("", response_model=ProjectPageRead)
def list_projects(
    session: Annotated[Session, Depends(get_db_session)],
    search: Annotated[str | None, Query(max_length=300)] = None,
    state: Literal["active", "archived", "all"] = "active",
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    statement = select(Project, ProjectMetadata).outerjoin(
        ProjectMetadata, ProjectMetadata.project_id == Project.id
    )
    if state == "active":
        statement = statement.where(ProjectMetadata.archived_at.is_(None))
    elif state == "archived":
        statement = statement.where(ProjectMetadata.archived_at.is_not(None))
    if search and search.strip():
        term = f"%{search.strip()}%"
        statement = statement.where(
            Project.name.ilike(term)
            | Project.target_url.ilike(term)
            | ProjectMetadata.environment.ilike(term)
        )
    total = session.scalar(select(func.count()).select_from(statement.with_only_columns(Project.id).subquery())) or 0
    rows = session.execute(
        statement.order_by(Project.updated_at.desc(), Project.id)
        .offset(offset)
        .limit(limit)
    ).all()
    ids = [project.id for project, _metadata in rows]
    stats = project_stats(session, ids)
    return {
        "items": [
            project_response(project, metadata, stats[project.id])
            for project, metadata in rows
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.post("", response_model=ProjectRead, status_code=status.HTTP_201_CREATED)
def create_project(
    request_data: ProjectCreate,
    session: Annotated[Session, Depends(get_db_session)],
) -> dict:
    project = Project(name=request_data.name, target_url=request_data.target_url)
    session.add(project)
    session.flush()
    metadata = ProjectMetadata(
        project_id=project.id,
        description=request_data.description,
        environment=request_data.environment,
    )
    session.add(metadata)
    session.commit()
    session.refresh(project)
    return project_response(project, metadata, project_stats(session, [project.id])[project.id])


@router.get("/{project_id}", response_model=ProjectRead)
def get_project_detail(
    project_id: UUID,
    session: Annotated[Session, Depends(get_db_session)],
) -> dict:
    return _read_project(session, project_id)


@router.patch("/{project_id}", response_model=ProjectRead)
def update_project(
    project_id: UUID,
    request_data: ProjectUpdate,
    session: Annotated[Session, Depends(get_db_session)],
) -> dict:
    try:
        project = get_project(session, project_id, lock=True)
    except ProjectNotFound as exc:
        raise _project_not_found() from exc
    updates = request_data.model_dump(exclude_unset=True)
    for field in ("name", "target_url"):
        if field in updates and updates[field] is None:
            raise HTTPException(status_code=422, detail=f"{field} cannot be null.")
    metadata = get_project_metadata(session, project.id, create=True)
    assert metadata is not None
    for field in ("name", "target_url"):
        if field in updates:
            setattr(project, field, updates[field])
    for field in ("description", "environment"):
        if field in updates:
            setattr(metadata, field, updates[field] or "" if field == "description" else updates[field])
    project.updated_at = datetime.now(UTC)
    session.commit()
    return project_response(project, metadata, project_stats(session, [project.id])[project.id])


@router.post("/{project_id}/archive", response_model=ProjectRead)
def archive_project_route(
    project_id: UUID,
    session: Annotated[Session, Depends(get_db_session)],
) -> dict:
    try:
        archive_project(session, project_id)
    except ProjectNotFound as exc:
        session.rollback()
        raise _project_not_found() from exc
    session.commit()
    return _read_project(session, project_id)


@router.post("/{project_id}/restore", response_model=ProjectRead)
def restore_project_route(
    project_id: UUID,
    session: Annotated[Session, Depends(get_db_session)],
) -> dict:
    try:
        restore_project(session, project_id)
    except ProjectNotFound as exc:
        session.rollback()
        raise _project_not_found() from exc
    session.commit()
    return _read_project(session, project_id)


@router.put(
    "/{project_id}/explorations/{exploration_id}",
    response_model=ProjectExplorationAssociationRead,
    status_code=status.HTTP_201_CREATED,
)
def assign_exploration(
    project_id: UUID,
    exploration_id: UUID,
    session: Annotated[Session, Depends(get_db_session)],
) -> dict:
    try:
        project = assert_project_active(session, project_id)
    except ProjectNotFound as exc:
        raise _project_not_found() from exc
    except ArchivedProjectError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "project_archived", "message": str(exc)},
        ) from exc
    exploration = session.get(Exploration, exploration_id)
    if exploration is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "exploration_not_found", "message": "Exploration was not found."},
        )
    current = session.get(ProjectExploration, exploration_id)
    if current is not None:
        if current.project_id != project.id:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "exploration_already_assigned",
                    "message": "An exploration can belong to only one project at a time.",
                },
            )
        sync_workflow_project_association(session, exploration_id, project.id)
        session.commit()
        return {"project_id": current.project_id, "exploration_id": current.exploration_id, "created_at": current.created_at}
    association = ProjectExploration(project_id=project.id, exploration_id=exploration.id)
    session.add(association)
    sync_workflow_project_association(session, exploration_id, project.id)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        current = session.get(ProjectExploration, exploration_id)
        if current is not None and current.project_id == project.id:
            return {"project_id": current.project_id, "exploration_id": current.exploration_id, "created_at": current.created_at}
        if current is not None:
            raise HTTPException(
                status_code=409,
                detail={"code": "exploration_already_assigned", "message": "An exploration can belong to only one project at a time."},
            ) from exc
        raise
    session.refresh(association)
    return {"project_id": association.project_id, "exploration_id": association.exploration_id, "created_at": association.created_at}


@router.delete("/{project_id}/explorations/{exploration_id}", status_code=status.HTTP_204_NO_CONTENT)
def unassign_exploration(
    project_id: UUID,
    exploration_id: UUID,
    session: Annotated[Session, Depends(get_db_session)],
) -> Response:
    try:
        get_project(session, project_id)
    except ProjectNotFound as exc:
        raise _project_not_found() from exc
    association = session.get(ProjectExploration, exploration_id)
    if association is None or association.project_id != project_id:
        raise HTTPException(
            status_code=404,
            detail={"code": "project_exploration_not_found", "message": "Project exploration association was not found."},
        )
    session.delete(association)
    sync_workflow_project_association(session, exploration_id, None)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{project_id}/explorations", response_model=ProjectExplorationPageRead)
def list_project_explorations(
    project_id: UUID,
    session: Annotated[Session, Depends(get_db_session)],
    search: Annotated[str | None, Query(max_length=300)] = None,
    exploration_status: Literal["queued", "running", "completed", "failed", "cancelled"] | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    try:
        get_project(session, project_id)
    except ProjectNotFound as exc:
        raise _project_not_found() from exc
    statement = (
        select(Exploration)
        .join(ProjectExploration, ProjectExploration.exploration_id == Exploration.id)
        .where(ProjectExploration.project_id == project_id)
    )
    if search and search.strip():
        statement = statement.where(Exploration.start_url.ilike(f"%{search.strip()}%"))
    if exploration_status:
        statement = statement.where(Exploration.status == exploration_status)
    total = session.scalar(select(func.count()).select_from(statement.subquery())) or 0
    explorations = list(
        session.scalars(
            statement.order_by(Exploration.created_at.desc(), Exploration.id.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    counts = _exploration_counts(session, [item.id for item in explorations])
    return {
        "items": [
            {
                "id": item.id,
                "start_url": item.start_url,
                "status": item.status,
                "pages_discovered": item.pages_discovered,
                "elements_discovered": item.elements_discovered,
                "event_count": item.event_count,
                "created_at": item.created_at,
                "completed_at": item.completed_at,
                "test_case_count": counts[item.id]["test_cases"],
                "test_run_count": counts[item.id]["test_runs"],
                "knowledge_graph_available": bool(counts[item.id]["nodes"]),
            }
            for item in explorations
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/{project_id}/test-cases", response_model=ProjectTestCasePageRead)
def list_project_test_cases(
    project_id: UUID,
    session: Annotated[Session, Depends(get_db_session)],
    search: Annotated[str | None, Query(max_length=300)] = None,
    source: Literal["ai_generated", "user_created"] | None = None,
    case_status: Literal["active", "superseded", "deleted"] | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    try:
        get_project(session, project_id)
    except ProjectNotFound as exc:
        raise _project_not_found() from exc
    statement = (
        select(TestCase, Exploration.start_url)
        .join(ProjectExploration, ProjectExploration.exploration_id == TestCase.exploration_id)
        .join(Exploration, Exploration.id == TestCase.exploration_id)
        .where(ProjectExploration.project_id == project_id)
    )
    if search and search.strip():
        statement = statement.where(TestCase.name.ilike(f"%{search.strip()}%"))
    if source:
        statement = statement.where(TestCase.source == source)
    if case_status == "active":
        statement = statement.where(TestCase.deleted_at.is_(None), TestCase.is_current.is_(True))
    elif case_status == "superseded":
        statement = statement.where(TestCase.deleted_at.is_(None), TestCase.is_current.is_(False))
    elif case_status == "deleted":
        statement = statement.where(TestCase.deleted_at.is_not(None))
    total = session.scalar(select(func.count()).select_from(statement.subquery())) or 0
    rows = session.execute(
        statement.order_by(TestCase.updated_at.desc(), TestCase.id)
        .limit(limit)
        .offset(offset)
    ).all()
    return {
        "items": [
            {
                "id": case_record.id,
                "exploration_id": case_record.exploration_id,
                "exploration_url": exploration_url,
                "source": case_record.source,
                "status": case_record.status,
                "name": case_record.name,
                "description": case_record.description,
                "created_at": case_record.created_at,
                "updated_at": case_record.updated_at,
            }
            for case_record, exploration_url in rows
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/{project_id}/run-history", response_model=ProjectRunPageRead)
def list_project_run_history(
    project_id: UUID,
    session: Annotated[Session, Depends(get_db_session)],
    run_status: Literal["pending", "running", "passed", "failed", "cancelled"] | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    try:
        get_project(session, project_id)
    except ProjectNotFound as exc:
        raise _project_not_found() from exc
    statement = (
        select(TestRun, Exploration.start_url, func.count(TestRunStep.id))
        .join(ProjectExploration, ProjectExploration.exploration_id == TestRun.exploration_id)
        .join(Exploration, Exploration.id == TestRun.exploration_id)
        .outerjoin(TestRunStep, TestRunStep.test_run_id == TestRun.id)
        .where(ProjectExploration.project_id == project_id)
        .group_by(TestRun.id, Exploration.start_url)
    )
    if run_status:
        statement = statement.where(TestRun.status == run_status)
    total = session.scalar(select(func.count()).select_from(statement.subquery())) or 0
    rows = session.execute(
        statement.order_by(TestRun.created_at.desc(), TestRun.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return {
        "items": [
            {
                "id": run.id,
                "exploration_id": run.exploration_id,
                "exploration_url": exploration_url,
                "test_case_id": run.test_case_id,
                "status": run.status,
                "error_message": run.error_message,
                "step_count": step_count,
                "created_at": run.created_at,
                "started_at": run.started_at,
                "completed_at": run.completed_at,
            }
            for run, exploration_url, step_count in rows
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/{project_id}/knowledge-graphs", response_model=ProjectGraphPageRead)
def list_project_knowledge_graphs(
    project_id: UUID,
    session: Annotated[Session, Depends(get_db_session)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    try:
        get_project(session, project_id)
    except ProjectNotFound as exc:
        raise _project_not_found() from exc
    nodes = (
        select(KnowledgeGraphNode.exploration_id.label("exploration_id"), func.count().label("count"))
        .group_by(KnowledgeGraphNode.exploration_id)
        .subquery()
    )
    edges = (
        select(KnowledgeGraphEdge.exploration_id.label("exploration_id"), func.count().label("count"))
        .group_by(KnowledgeGraphEdge.exploration_id)
        .subquery()
    )
    statement = (
        select(
            Exploration.id,
            Exploration.start_url,
            Exploration.status,
            func.coalesce(nodes.c.count, 0),
            func.coalesce(edges.c.count, 0),
        )
        .join(ProjectExploration, ProjectExploration.exploration_id == Exploration.id)
        .outerjoin(nodes, nodes.c.exploration_id == Exploration.id)
        .outerjoin(edges, edges.c.exploration_id == Exploration.id)
        .where(ProjectExploration.project_id == project_id)
    )
    total = session.scalar(select(func.count()).select_from(statement.subquery())) or 0
    rows = session.execute(
        statement.order_by(Exploration.created_at.desc(), Exploration.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return {
        "items": [
            {
                "exploration_id": exploration_id,
                "exploration_url": url,
                "exploration_status": exploration_status,
                "node_count": node_count,
                "edge_count": edge_count,
                "available": node_count > 0,
            }
            for exploration_id, url, exploration_status, node_count, edge_count in rows
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }
