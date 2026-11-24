from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, Index, JSON, String, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.ownership import OwnedRecord


class KnowledgeGraphNode(OwnedRecord, Base):
    __tablename__ = "knowledge_graph_nodes"
    __table_args__ = (
        CheckConstraint(
            "node_type IN ('exploration', 'page', 'ui_element', 'form', 'workflow', 'test_case', 'interaction_state')",
            name="ck_knowledge_graph_nodes_type",
        ),
        CheckConstraint("provenance IN ('verified', 'suggested')", name="ck_knowledge_graph_nodes_provenance"),
        UniqueConstraint("exploration_id", "node_type", "source_key", name="uq_knowledge_graph_nodes_source"),
        Index("ix_knowledge_graph_nodes_exploration_type", "exploration_id", "node_type"),
        Index("ix_knowledge_graph_nodes_exploration_page", "exploration_id", "page_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    # Relationships to source rows are checked by the graph service because this
    # deployment role does not have REFERENCES privileges on the source tables.
    exploration_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    node_type: Mapped[str] = mapped_column(String(30), nullable=False)
    source_key: Mapped[str] = mapped_column(String(300), nullable=False)
    label: Mapped[str] = mapped_column(String(300), nullable=False)
    page_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    provenance: Mapped[str] = mapped_column(String(16), nullable=False, default="verified")
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    properties: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class KnowledgeGraphEdge(OwnedRecord, Base):
    __tablename__ = "knowledge_graph_edges"
    __table_args__ = (
        CheckConstraint(
            "edge_type IN ('contains_page', 'contains_element', 'links_to', 'belongs_to_form', "
            "'participates_in_workflow', 'has_test_case', 'transitions_to_state')",
            name="ck_knowledge_graph_edges_type",
        ),
        CheckConstraint("provenance IN ('verified', 'suggested')", name="ck_knowledge_graph_edges_provenance"),
        UniqueConstraint(
            "exploration_id", "source_node_id", "target_node_id", "edge_type", "provenance",
            name="uq_knowledge_graph_edges_relation",
        ),
        Index("ix_knowledge_graph_edges_exploration", "exploration_id"),
        Index("ix_knowledge_graph_edges_source", "exploration_id", "source_node_id"),
        Index("ix_knowledge_graph_edges_target", "exploration_id", "target_node_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    exploration_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_node_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    target_node_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    edge_type: Mapped[str] = mapped_column(String(40), nullable=False)
    provenance: Mapped[str] = mapped_column(String(16), nullable=False, default="verified")
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    properties: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
