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
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.ownership import OwnedRecord


class Exploration(OwnedRecord, Base):
    __tablename__ = "explorations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed', 'cancelled')",
            name="ck_explorations_status",
        ),
        Index("ix_explorations_created_at", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    start_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    current_url: Mapped[str | None] = mapped_column(String(2048))
    max_pages: Mapped[int] = mapped_column(Integer, nullable=False)
    max_depth: Mapped[int] = mapped_column(Integer, nullable=False)
    pages_discovered: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    elements_discovered: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    event_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    pages: Mapped[list["ExplorationPage"]] = relationship(
        back_populates="exploration", cascade="all, delete-orphan"
    )
    events: Mapped[list["ExplorationEvent"]] = relationship(
        back_populates="exploration", cascade="all, delete-orphan"
    )
    analysis: Mapped["ExplorationAnalysis | None"] = relationship(
        back_populates="exploration", cascade="all, delete-orphan", uselist=False
    )


class ExplorationAnalysis(OwnedRecord, Base):
    __tablename__ = "exploration_analyses"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed')",
            name="ck_exploration_analyses_status",
        ),
        UniqueConstraint(
            "exploration_id", name="uq_exploration_analyses_exploration_id"
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    exploration_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("explorations.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    model: Mapped[str | None] = mapped_column(String(200))
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error_message: Mapped[str | None] = mapped_column(Text)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    exploration: Mapped[Exploration] = relationship(back_populates="analysis")


class ExplorationPage(OwnedRecord, Base):
    __tablename__ = "exploration_pages"
    __table_args__ = (
        UniqueConstraint("exploration_id", "url", name="uq_exploration_pages_url"),
        Index("ix_exploration_pages_exploration_depth", "exploration_id", "depth"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    exploration_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("explorations.id", ondelete="CASCADE"), nullable=False
    )
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    title: Mapped[str] = mapped_column(String(1000), nullable=False, default="")
    description: Mapped[str | None] = mapped_column(Text)
    depth: Mapped[int] = mapped_column(Integer, nullable=False)
    http_status: Mapped[int | None] = mapped_column(Integer)
    visible_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    dom_outline: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    screenshot: Mapped[bytes | None] = mapped_column(LargeBinary)
    screenshot_mime: Mapped[str | None] = mapped_column(String(80))
    element_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    exploration: Mapped[Exploration] = relationship(back_populates="pages")
    elements: Mapped[list["ObservedElement"]] = relationship(
        back_populates="page", cascade="all, delete-orphan"
    )


class ObservedElement(OwnedRecord, Base):
    __tablename__ = "observed_elements"
    __table_args__ = (
        Index("ix_observed_elements_page_kind", "page_id", "kind"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    page_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("exploration_pages.id", ondelete="CASCADE"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    tag_name: Mapped[str] = mapped_column(String(40), nullable=False)
    role: Mapped[str | None] = mapped_column(String(80))
    accessible_name: Mapped[str | None] = mapped_column(String(1000))
    label: Mapped[str | None] = mapped_column(String(1000))
    text: Mapped[str | None] = mapped_column(String(2000))
    selector: Mapped[str | None] = mapped_column(String(1000))
    attributes: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    page: Mapped[ExplorationPage] = relationship(back_populates="elements")


class ExplorationEvent(OwnedRecord, Base):
    __tablename__ = "exploration_events"
    __table_args__ = (
        UniqueConstraint("exploration_id", "sequence", name="uq_exploration_events_sequence"),
        Index("ix_exploration_events_exploration_created", "exploration_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    exploration_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("explorations.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(60), nullable=False)
    message: Mapped[str] = mapped_column(String(1000), nullable=False)
    url: Mapped[str | None] = mapped_column(String(2048))
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    exploration: Mapped[Exploration] = relationship(back_populates="events")
