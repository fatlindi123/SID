from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Index, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.ownership import OwnedRecord


class Project(OwnedRecord, Base):
    __tablename__ = "projects"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    target_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class ProjectMetadata(OwnedRecord, Base):
    """Fields added to legacy projects without altering the owned-by-other-role table."""

    __tablename__ = "project_metadata"

    project_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    environment: Mapped[str | None] = mapped_column(String(100))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProjectExploration(OwnedRecord, Base):
    """One-to-one assignment records; IDs are validated against source tables in services."""

    __tablename__ = "project_explorations"
    __table_args__ = (
        Index("ix_project_explorations_project", "project_id"),
    )

    exploration_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    project_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
