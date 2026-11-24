from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, Index, String, Uuid, func, select
from sqlalchemy.orm import Mapped, mapped_column, object_session

from app.db.base import Base


class OwnedRecord:
    """Workspace record whose ownership is stored in an additive sidecar table."""

    @property
    def owner_id(self) -> UUID | None:
        transient_owner = self.__dict__.get("_owner_id")
        if transient_owner is not None:
            return transient_owner
        session = object_session(self)
        if session is None:
            return None
        from sqlalchemy import inspect

        key = inspect(self).identity
        if key is None:
            return None
        return session.scalar(
            select(WorkspaceRecordOwner.owner_id).where(
                WorkspaceRecordOwner.resource_type == self.__tablename__,
                WorkspaceRecordOwner.resource_id == key[0],
            )
        )

    @owner_id.setter
    def owner_id(self, value: UUID | None) -> None:
        self.__dict__["_owner_id"] = value


class WorkspaceRecordOwner(Base):
    """Application-enforced owner index for legacy tables the DB role cannot alter."""

    __tablename__ = "workspace_record_owners"
    __table_args__ = (Index("ix_workspace_record_owners_owner_type", "owner_id", "resource_type"),)

    resource_type: Mapped[str] = mapped_column(String(64), primary_key=True)
    resource_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    owner_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
