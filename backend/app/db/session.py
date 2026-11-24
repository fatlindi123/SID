from collections.abc import Generator
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import create_engine, exists, event, inspect, select
from sqlalchemy.orm import Session, sessionmaker, with_loader_criteria

from app.core.config import get_settings
from app.models.ownership import OwnedRecord, WorkspaceRecordOwner

settings = get_settings()
engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _owned_models() -> tuple[type[OwnedRecord], ...]:
    from app.models.exploration import Exploration, ExplorationAnalysis, ExplorationEvent, ExplorationPage, ObservedElement
    from app.models.interaction_observation import InteractionObservation
    from app.models.knowledge_graph import KnowledgeGraphEdge, KnowledgeGraphNode
    from app.models.project import Project, ProjectExploration, ProjectMetadata
    from app.models.report import GeneratedReport
    from app.models.test_case import TestCase
    from app.models.test_execution import TestRun, TestRunStep
    from app.models.workflow import Workflow, WorkflowStep

    return (
        Exploration, ExplorationAnalysis, ExplorationEvent, ExplorationPage, ObservedElement,
        InteractionObservation, KnowledgeGraphEdge, KnowledgeGraphNode, Project, ProjectExploration,
        ProjectMetadata, GeneratedReport, TestCase, TestRun, TestRunStep, Workflow, WorkflowStep,
    )


def _record_identity(record: OwnedRecord) -> UUID:
    primary_key = inspect(type(record)).primary_key
    if len(primary_key) != 1:
        raise TypeError("Workspace ownership supports records with one stable UUID primary key.")
    value = getattr(record, primary_key[0].key)
    if value is None:
        value = uuid4()
        setattr(record, primary_key[0].key, value)
    return value


def _stored_owner(session: Session, record_type: str, record_id: UUID) -> UUID | None:
    return session.scalar(select(WorkspaceRecordOwner.owner_id).where(
        WorkspaceRecordOwner.resource_type == record_type,
        WorkspaceRecordOwner.resource_id == record_id,
    ))


def _parent_refs(record: OwnedRecord) -> list[tuple[type[OwnedRecord], UUID]]:
    from app.models.exploration import Exploration, ExplorationAnalysis, ExplorationEvent, ExplorationPage, ObservedElement
    from app.models.knowledge_graph import KnowledgeGraphEdge, KnowledgeGraphNode
    from app.models.project import Project, ProjectExploration, ProjectMetadata
    from app.models.report import GeneratedReport
    from app.models.test_case import TestCase
    from app.models.test_execution import TestRun, TestRunStep
    from app.models.workflow import Workflow, WorkflowStep

    if isinstance(record, ProjectMetadata):
        return [(Project, record.project_id)]
    if isinstance(record, ProjectExploration):
        return [(Project, record.project_id), (Exploration, record.exploration_id)]
    if isinstance(record, GeneratedReport):
        parents = []
        if record.exploration_id:
            parents.append((Exploration, record.exploration_id))
        if record.project_id:
            parents.append((Project, record.project_id))
        return parents
    if isinstance(record, ObservedElement):
        return [(ExplorationPage, record.page_id)]
    if isinstance(record, TestRunStep):
        return [(TestRun, record.test_run_id)]
    if isinstance(record, WorkflowStep):
        return [(Workflow, record.workflow_id)]
    if isinstance(record, (ExplorationPage, TestCase, TestRun, Workflow, ExplorationAnalysis,
                           ExplorationEvent, KnowledgeGraphEdge, KnowledgeGraphNode)):
        return [(Exploration, record.exploration_id)]
    from app.models.interaction_observation import InteractionObservation

    if isinstance(record, InteractionObservation):
        return [(Exploration, record.exploration_id)]
    return []


def _pending_owner(session: Session, parent_type: type[OwnedRecord], parent_id: UUID) -> UUID | None:
    for pending in session.new:
        if isinstance(pending, parent_type) and _record_identity(pending) == parent_id:
            return pending.__dict__.get("_owner_id")
    return None


def _infer_parent_owner(session: Session, record: OwnedRecord) -> UUID | None:
    owners: set[UUID] = set()
    for parent_type, parent_id in _parent_refs(record):
        owner_id = _pending_owner(session, parent_type, parent_id)
        if owner_id is None:
            owner_id = _stored_owner(session, parent_type.__tablename__, parent_id)
        if owner_id is not None:
            owners.add(owner_id)
    if len(owners) > 1:
        raise PermissionError("Workspace record ownership validation failed.")
    return next(iter(owners), None)


def _owner_predicate(model: type[OwnedRecord], owner_id: UUID):
    primary_key = inspect(model).primary_key
    if len(primary_key) != 1:
        raise TypeError("Workspace ownership supports records with one stable UUID primary key.")
    return exists(select(1).where(
        WorkspaceRecordOwner.resource_type == model.__tablename__,
        WorkspaceRecordOwner.resource_id == primary_key[0],
        WorkspaceRecordOwner.owner_id == owner_id,
    ).correlate(model))


@event.listens_for(Session, "do_orm_execute")
def _scope_owned_reads_and_bulk_writes(state: Any) -> None:
    owner_id = state.session.info.get("owner_id")
    if owner_id is None or state.execution_options.get("skip_owner_scope"):
        return
    if not (state.is_select or state.is_update or state.is_delete):
        return
    statement = state.statement
    for model in _owned_models():
        statement = statement.options(
            with_loader_criteria(model, _owner_predicate(model, owner_id), include_aliases=True)
        )
    state.statement = statement


@event.listens_for(Session, "before_flush")
def _assign_and_validate_owner(session: Session, _flush_context: Any, _instances: Any) -> None:
    from app.models.exploration import Exploration
    from app.models.project import Project

    owner_id = session.info.get("owner_id")
    new_records = [record for record in session.new if isinstance(record, OwnedRecord)]
    roots = (Exploration, Project)
    for record in new_records:
        if isinstance(record, roots):
            explicit_owner = record.__dict__.get("_owner_id")
            if owner_id is not None and explicit_owner not in (None, owner_id):
                raise PermissionError("Workspace record ownership validation failed.")
            record.owner_id = owner_id or explicit_owner
            _record_identity(record)

    for record in new_records:
        explicit_owner = record.__dict__.get("_owner_id")
        parent_owner = _infer_parent_owner(session, record) if not isinstance(record, roots) else None
        if owner_id is not None:
            if explicit_owner not in (None, owner_id) or parent_owner not in (None, owner_id):
                raise PermissionError("Workspace record ownership validation failed.")
            if not isinstance(record, roots) and _parent_refs(record) and parent_owner is None:
                raise PermissionError("Workspace record parent ownership could not be verified.")
            actual_owner = owner_id
        else:
            if explicit_owner is not None and parent_owner is not None and explicit_owner != parent_owner:
                raise PermissionError("Workspace record ownership validation failed.")
            actual_owner = explicit_owner or parent_owner
        if actual_owner is None:
            continue
        record.owner_id = actual_owner
        session.add(WorkspaceRecordOwner(
            resource_type=record.__tablename__,
            resource_id=_record_identity(record),
            owner_id=actual_owner,
        ))

    if owner_id is not None:
        for record in session.dirty.union(session.deleted):
            if isinstance(record, OwnedRecord) and record not in session.new:
                actual_owner = _stored_owner(session, record.__tablename__, _record_identity(record))
                if actual_owner != owner_id:
                    raise PermissionError("Workspace record ownership validation failed.")


def get_db_session() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        yield session
