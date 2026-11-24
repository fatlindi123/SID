from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    JSON,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.ownership import OwnedRecord


class InteractionObservation(OwnedRecord, Base):
    __tablename__ = "interaction_observations"
    __table_args__ = (
        CheckConstraint(
            "outcome IN ('observed', 'skipped', 'blocked', 'failed')",
            name="ck_interaction_observations_outcome",
        ),
        UniqueConstraint(
            "exploration_id", "page_id", "probe_key",
            name="uq_interaction_observations_probe",
        ),
        Index(
            "ix_interaction_observations_exploration_page",
            "exploration_id", "page_id",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    # These IDs intentionally have no database FKs. The current deployment role
    # cannot add REFERENCES permissions to the existing exploration tables.
    # The API verifies both IDs belong to the requested exploration on read.
    exploration_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    page_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    element_ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    probe_key: Mapped[str] = mapped_column(String(64), nullable=False)
    action_type: Mapped[str] = mapped_column(String(20), nullable=False)
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(160))
    before_evidence: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    after_evidence: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
