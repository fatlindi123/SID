from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, Index, JSON, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.ownership import OwnedRecord


class GeneratedReport(OwnedRecord, Base):
    __tablename__ = "reports"
    __table_args__ = (
        CheckConstraint("report_type IN ('exploration', 'project')", name="ck_reports_type"),
        CheckConstraint("status IN ('running', 'completed', 'failed')", name="ck_reports_status"),
        CheckConstraint(
            "(report_type = 'exploration' AND exploration_id IS NOT NULL) "
            "OR (report_type = 'project' AND project_id IS NOT NULL AND exploration_id IS NULL)",
            name="ck_reports_scope",
        ),
        Index("ix_reports_generated_at", "generated_at"),
        Index("ix_reports_project_generated", "project_id", "generated_at"),
        Index("ix_reports_exploration_generated", "exploration_id", "generated_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    report_type: Mapped[str] = mapped_column(String(20), nullable=False)
    project_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    exploration_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    scope_label: Mapped[str] = mapped_column(String(300), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running")
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    filters: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error_code: Mapped[str | None] = mapped_column(String(60))
