from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Boolean, CheckConstraint, DateTime, Index, JSON, String, Text, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.ownership import OwnedRecord


class TestCase(OwnedRecord, Base):
    __test__ = False
    __tablename__ = "test_cases"
    __table_args__ = (
        CheckConstraint("source IN ('ai_generated', 'user_created')", name="ck_test_cases_source"),
        CheckConstraint(
            "(source = 'ai_generated' AND source_analysis_id IS NOT NULL AND analysis_version_hash IS NOT NULL) "
            "OR (source = 'user_created' AND source_analysis_id IS NULL AND analysis_version_hash IS NULL)",
            name="ck_test_cases_source_analysis",
        ),
        UniqueConstraint("exploration_id", "source", "identity_hash", name="uq_test_cases_identity"),
        Index("ix_test_cases_exploration_current", "exploration_id", "is_current"),
        Index("ix_test_cases_exploration_name", "exploration_id", "name"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    # Existing database roles cannot reference the exploration or analysis tables.
    # API and synchronization services validate those relationships explicitly.
    exploration_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    identity_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_analysis_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    analysis_version_hash: Mapped[str | None] = mapped_column(String(64))
    scenario_version_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    definition: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    runs: Mapped[list["TestRun"]] = relationship(back_populates="test_case")

    @property
    def status(self) -> str:
        if self.deleted_at is not None:
            return "deleted"
        return "active" if self.is_current else "superseded"
