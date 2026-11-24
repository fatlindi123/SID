"""add persistent evidence-based knowledge graph"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0008_evidence_knowledge_graph"
down_revision: Union[str, None] = "0007_test_case_management"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "knowledge_graph_nodes",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("exploration_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("node_type", sa.String(length=30), nullable=False),
        sa.Column("source_key", sa.String(length=300), nullable=False),
        sa.Column("label", sa.String(length=300), nullable=False),
        sa.Column("page_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("provenance", sa.String(length=16), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("properties", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "node_type IN ('exploration', 'page', 'ui_element', 'form', 'workflow', 'test_case', 'interaction_state')",
            name="ck_knowledge_graph_nodes_type",
        ),
        sa.CheckConstraint("provenance IN ('verified', 'suggested')", name="ck_knowledge_graph_nodes_provenance"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("exploration_id", "node_type", "source_key", name="uq_knowledge_graph_nodes_source"),
    )
    op.create_index(
        "ix_knowledge_graph_nodes_exploration_type",
        "knowledge_graph_nodes",
        ["exploration_id", "node_type"],
    )
    op.create_index(
        "ix_knowledge_graph_nodes_exploration_page",
        "knowledge_graph_nodes",
        ["exploration_id", "page_id"],
    )
    op.create_table(
        "knowledge_graph_edges",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("exploration_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("source_node_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("target_node_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("edge_type", sa.String(length=40), nullable=False),
        sa.Column("provenance", sa.String(length=16), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("properties", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "edge_type IN ('contains_page', 'contains_element', 'links_to', 'belongs_to_form', "
            "'participates_in_workflow', 'has_test_case', 'transitions_to_state')",
            name="ck_knowledge_graph_edges_type",
        ),
        sa.CheckConstraint("provenance IN ('verified', 'suggested')", name="ck_knowledge_graph_edges_provenance"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "exploration_id", "source_node_id", "target_node_id", "edge_type", "provenance",
            name="uq_knowledge_graph_edges_relation",
        ),
    )
    op.create_index("ix_knowledge_graph_edges_exploration", "knowledge_graph_edges", ["exploration_id"])
    op.create_index(
        "ix_knowledge_graph_edges_source",
        "knowledge_graph_edges",
        ["exploration_id", "source_node_id"],
    )
    op.create_index(
        "ix_knowledge_graph_edges_target",
        "knowledge_graph_edges",
        ["exploration_id", "target_node_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_knowledge_graph_edges_target", table_name="knowledge_graph_edges")
    op.drop_index("ix_knowledge_graph_edges_source", table_name="knowledge_graph_edges")
    op.drop_index("ix_knowledge_graph_edges_exploration", table_name="knowledge_graph_edges")
    op.drop_table("knowledge_graph_edges")
    op.drop_index("ix_knowledge_graph_nodes_exploration_page", table_name="knowledge_graph_nodes")
    op.drop_index("ix_knowledge_graph_nodes_exploration_type", table_name="knowledge_graph_nodes")
    op.drop_table("knowledge_graph_nodes")
