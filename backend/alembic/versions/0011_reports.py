"""persist immutable exploration and project report snapshots"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0011_reports"
down_revision: Union[str, None] = "0010_workflow_intelligence"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Project and exploration IDs are validated by the report service. The
    # configured role cannot add REFERENCES constraints to the source tables.
    op.create_table(
        "reports",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("report_type", sa.String(length=20), nullable=False),
        sa.Column("project_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("exploration_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("scope_label", sa.String(length=300), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="running", nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("filters", sa.JSON(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=True),
        sa.Column("error_code", sa.String(length=60), nullable=True),
        sa.CheckConstraint("report_type IN ('exploration', 'project')", name="ck_reports_type"),
        sa.CheckConstraint("status IN ('running', 'completed', 'failed')", name="ck_reports_status"),
        sa.CheckConstraint(
            "(report_type = 'exploration' AND exploration_id IS NOT NULL) "
            "OR (report_type = 'project' AND project_id IS NOT NULL AND exploration_id IS NULL)",
            name="ck_reports_scope",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_reports_generated_at", "reports", ["generated_at"])
    op.create_index("ix_reports_project_generated", "reports", ["project_id", "generated_at"])
    op.create_index("ix_reports_exploration_generated", "reports", ["exploration_id", "generated_at"])


def downgrade() -> None:
    op.drop_index("ix_reports_exploration_generated", table_name="reports")
    op.drop_index("ix_reports_project_generated", table_name="reports")
    op.drop_index("ix_reports_generated_at", table_name="reports")
    op.drop_table("reports")
