from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models.exploration import Exploration
from app.models.knowledge_graph import KnowledgeGraphNode
from app.models.project import Project, ProjectExploration, ProjectMetadata
from app.models.test_case import TestCase
from app.models.test_execution import TestRun


class ProjectNotFound(LookupError):
    pass


class ArchivedProjectError(RuntimeError):
    pass


class ProjectAssociationConflict(RuntimeError):
    pass


class InvalidProjectAssociation(RuntimeError):
    pass


def get_project(session: Session, project_id: UUID, *, lock: bool = False) -> Project:
    statement = select(Project).where(Project.id == project_id)
    if lock:
        statement = statement.with_for_update()
    project = session.scalar(statement)
    if project is None:
        raise ProjectNotFound("Project was not found.")
    return project


def get_project_metadata(session: Session, project_id: UUID, *, create: bool = False) -> ProjectMetadata | None:
    metadata = session.get(ProjectMetadata, project_id)
    if metadata is None and create:
        metadata = ProjectMetadata(project_id=project_id, description="")
        session.add(metadata)
        session.flush()
    return metadata


def assert_project_active(session: Session, project_id: UUID) -> Project:
    project = get_project(session, project_id, lock=True)
    metadata = get_project_metadata(session, project_id)
    if metadata is not None and metadata.archived_at is not None:
        raise ArchivedProjectError("Archived projects cannot accept new explorations or test runs.")
    return project


def ensure_exploration_project_active(session: Session, exploration_id: UUID) -> None:
    project_id = session.scalar(
        select(ProjectExploration.project_id).where(ProjectExploration.exploration_id == exploration_id)
    )
    if project_id is None:
        return
    try:
        assert_project_active(session, project_id)
    except ProjectNotFound as exc:
        raise InvalidProjectAssociation("The exploration refers to a project that no longer exists.") from exc


def project_stats(session: Session, project_ids: list[UUID]) -> dict[UUID, dict[str, int]]:
    if not project_ids:
        return {}
    result: dict[UUID, dict[str, int]] = {
        project_id: {
            "exploration_count": 0,
            "active_exploration_count": 0,
            "completed_exploration_count": 0,
            "failed_exploration_count": 0,
            "test_case_count": 0,
            "test_run_count": 0,
            "pending_run_count": 0,
            "running_run_count": 0,
            "passed_run_count": 0,
            "failed_run_count": 0,
            "knowledge_graph_exploration_count": 0,
        }
        for project_id in project_ids
    }

    exploration_rows = session.execute(
        select(
            ProjectExploration.project_id,
            func.count(Exploration.id),
            func.sum(case((Exploration.status.in_(("queued", "running")), 1), else_=0)),
            func.sum(case((Exploration.status == "completed", 1), else_=0)),
            func.sum(case((Exploration.status == "failed", 1), else_=0)),
        )
        .join(Exploration, Exploration.id == ProjectExploration.exploration_id)
        .where(ProjectExploration.project_id.in_(project_ids))
        .group_by(ProjectExploration.project_id)
    ).all()
    for project_id, count, active, completed, failed in exploration_rows:
        result[project_id].update(
            exploration_count=int(count or 0),
            active_exploration_count=int(active or 0),
            completed_exploration_count=int(completed or 0),
            failed_exploration_count=int(failed or 0),
        )

    case_rows = session.execute(
        select(ProjectExploration.project_id, func.count(TestCase.id))
        .join(TestCase, TestCase.exploration_id == ProjectExploration.exploration_id)
        .where(
            ProjectExploration.project_id.in_(project_ids),
            TestCase.is_current.is_(True),
            TestCase.deleted_at.is_(None),
        )
        .group_by(ProjectExploration.project_id)
    ).all()
    for project_id, count in case_rows:
        result[project_id]["test_case_count"] = int(count or 0)

    run_rows = session.execute(
        select(
            ProjectExploration.project_id,
            func.count(TestRun.id),
            func.sum(case((TestRun.status == "pending", 1), else_=0)),
            func.sum(case((TestRun.status == "running", 1), else_=0)),
            func.sum(case((TestRun.status == "passed", 1), else_=0)),
            func.sum(case((TestRun.status == "failed", 1), else_=0)),
        )
        .join(TestRun, TestRun.exploration_id == ProjectExploration.exploration_id)
        .where(ProjectExploration.project_id.in_(project_ids))
        .group_by(ProjectExploration.project_id)
    ).all()
    for project_id, count, pending, running, passed, failed in run_rows:
        result[project_id].update(
            test_run_count=int(count or 0),
            pending_run_count=int(pending or 0),
            running_run_count=int(running or 0),
            passed_run_count=int(passed or 0),
            failed_run_count=int(failed or 0),
        )

    graph_rows = session.execute(
        select(ProjectExploration.project_id, func.count(func.distinct(KnowledgeGraphNode.exploration_id)))
        .join(KnowledgeGraphNode, KnowledgeGraphNode.exploration_id == ProjectExploration.exploration_id)
        .where(ProjectExploration.project_id.in_(project_ids))
        .group_by(ProjectExploration.project_id)
    ).all()
    for project_id, count in graph_rows:
        result[project_id]["knowledge_graph_exploration_count"] = int(count or 0)
    return result


def project_response(
    project: Project,
    metadata: ProjectMetadata | None,
    stats: dict[str, int],
) -> dict[str, Any]:
    return {
        "id": project.id,
        "name": project.name,
        "target_url": project.target_url,
        "description": metadata.description if metadata is not None else "",
        "environment": metadata.environment if metadata is not None else None,
        "archived": metadata.archived_at is not None if metadata is not None else False,
        "archived_at": metadata.archived_at if metadata is not None else None,
        "created_at": project.created_at,
        "updated_at": project.updated_at,
        "stats": stats,
    }


def archive_project(session: Session, project_id: UUID) -> ProjectMetadata:
    project = get_project(session, project_id, lock=True)
    metadata = get_project_metadata(session, project.id, create=True)
    assert metadata is not None
    if metadata.archived_at is None:
        metadata.archived_at = datetime.now(UTC)
        project.updated_at = metadata.archived_at
    return metadata


def restore_project(session: Session, project_id: UUID) -> ProjectMetadata:
    project = get_project(session, project_id, lock=True)
    metadata = get_project_metadata(session, project.id, create=True)
    assert metadata is not None
    if metadata.archived_at is not None:
        metadata.archived_at = None
        project.updated_at = datetime.now(UTC)
    return metadata
