"""add persisted evidence-based workflows and ordered steps"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0010_workflow_intelligence"
down_revision: Union[str, None] = "0009_projects_management"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Sidecar tables intentionally use application-validated source relationships;
    # the configured database role cannot add REFERENCES to the legacy tables.
    op.create_table(
        "workflows",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("exploration_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("project_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("identity_hash", sa.String(length=64), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_id", sa.String(length=64), nullable=False),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("provenance", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="active", nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("reviewed", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("source_type IN ('page_link', 'interaction_observation', 'test_case', 'manual')", name="ck_workflows_source_type"),
        sa.CheckConstraint("provenance IN ('observed', 'suggested', 'user_authored')", name="ck_workflows_provenance"),
        sa.CheckConstraint("status IN ('active', 'stale')", name="ck_workflows_status"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("exploration_id", "identity_hash", name="uq_workflows_exploration_identity"),
    )
    op.create_index("ix_workflows_exploration_status", "workflows", ["exploration_id", "status"])
    op.create_index("ix_workflows_project", "workflows", ["project_id"])
    op.create_table(
        "workflow_steps",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("workflow_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("step_type", sa.String(length=32), nullable=False),
        sa.Column("provenance", sa.String(length=20), nullable=False),
        sa.Column("instruction", sa.String(length=1000), nullable=False),
        sa.Column("page_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("target_page_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("page_url", sa.String(length=2048), nullable=False),
        sa.Column("page_title", sa.String(length=1000), server_default="", nullable=False),
        sa.Column("control_name", sa.String(length=200), nullable=True),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("observed_state", sa.JSON(), nullable=True),
        sa.Column("expected_state", sa.JSON(), nullable=True),
        sa.CheckConstraint("provenance IN ('observed', 'suggested', 'user_authored')", name="ck_workflow_steps_provenance"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workflow_id", "ordinal", name="uq_workflow_steps_ordinal"),
    )
    op.create_index("ix_workflow_steps_page", "workflow_steps", ["page_id"])


def downgrade() -> None:
    op.drop_index("ix_workflow_steps_page", table_name="workflow_steps")
    op.drop_table("workflow_steps")
    op.drop_index("ix_workflows_project", table_name="workflows")
    op.drop_index("ix_workflows_exploration_status", table_name="workflows")
    op.drop_table("workflows")
