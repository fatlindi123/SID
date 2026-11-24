from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.ownership import OwnedRecord


class TestRun(OwnedRecord, Base):
    __test__ = False
    __tablename__ = "test_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'running', 'passed', 'failed', 'cancelled')",
            name="ck_test_runs_status",
        ),
        Index("ix_test_runs_exploration_created", "exploration_id", "created_at"),
        Index("ix_test_runs_test_case_id", "test_case_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    exploration_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), nullable=False
    )
    analysis_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), nullable=True
    )
    scenario_index: Mapped[int] = mapped_column(Integer, nullable=False)
    test_case_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("test_cases.id", ondelete="RESTRICT")
    )
    scenario: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    steps: Mapped[list["TestRunStep"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="TestRunStep.ordinal"
    )
    test_case: Mapped["TestCase | None"] = relationship(back_populates="runs")


class TestRunStep(OwnedRecord, Base):
    __test__ = False
    __tablename__ = "test_run_steps"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'running', 'passed', 'failed', 'cancelled')",
            name="ck_test_run_steps_status",
        ),
        UniqueConstraint("test_run_id", "ordinal", name="uq_test_run_steps_ordinal"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    test_run_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("test_runs.id", ondelete="CASCADE"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    instruction: Mapped[str] = mapped_column(String(1000), nullable=False)
    action: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    expected_evidence: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    error_message: Mapped[str | None] = mapped_column(Text)
    actual_evidence: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    run: Mapped[TestRun] = relationship(back_populates="steps")
