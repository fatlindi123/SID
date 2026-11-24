from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Boolean, CheckConstraint, DateTime, Index, Integer, JSON, String, Text, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, foreign, mapped_column, relationship

from app.db.base import Base
from app.models.ownership import OwnedRecord


class Workflow(OwnedRecord, Base):
    __tablename__ = "workflows"
    __table_args__ = (
        CheckConstraint(
            "source_type IN ('page_link', 'interaction_observation', 'test_case', 'manual')",
            name="ck_workflows_source_type",
        ),
        CheckConstraint("provenance IN ('observed', 'suggested', 'user_authored')", name="ck_workflows_provenance"),
        CheckConstraint("status IN ('active', 'stale')", name="ck_workflows_status"),
        UniqueConstraint("exploration_id", "identity_hash", name="uq_workflows_exploration_identity"),
        Index("ix_workflows_exploration_status", "exploration_id", "status"),
        Index("ix_workflows_project", "project_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    # Source relationships are verified in the service because the DB role may not
    # have REFERENCES privileges on the legacy application tables.
    exploration_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    project_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    identity_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    provenance: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    reviewed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    steps: Mapped[list["WorkflowStep"]] = relationship(
        primaryjoin=lambda: Workflow.id == foreign(WorkflowStep.workflow_id),
        back_populates="workflow", cascade="all, delete-orphan", order_by="WorkflowStep.ordinal"
    )


class WorkflowStep(OwnedRecord, Base):
    __tablename__ = "workflow_steps"
    __table_args__ = (
        CheckConstraint("provenance IN ('observed', 'suggested', 'user_authored')", name="ck_workflow_steps_provenance"),
        UniqueConstraint("workflow_id", "ordinal", name="uq_workflow_steps_ordinal"),
        Index("ix_workflow_steps_page", "page_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    workflow_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    step_type: Mapped[str] = mapped_column(String(32), nullable=False)
    provenance: Mapped[str] = mapped_column(String(20), nullable=False)
    instruction: Mapped[str] = mapped_column(String(1000), nullable=False)
    page_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    target_page_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    page_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    page_title: Mapped[str] = mapped_column(String(1000), nullable=False, default="")
    control_name: Mapped[str | None] = mapped_column(String(200))
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    observed_state: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    expected_state: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    workflow: Mapped[Workflow] = relationship(
        primaryjoin=lambda: foreign(WorkflowStep.workflow_id) == Workflow.id,
        back_populates="steps",
    )
