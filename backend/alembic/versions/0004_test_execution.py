"""persist browser test runs and step results"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0004_test_execution"
down_revision: Union[str, None] = "0003_exploration_analyses"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "test_runs",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("exploration_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("analysis_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("scenario_index", sa.Integer(), nullable=False),
        sa.Column("scenario", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'passed', 'failed', 'cancelled')",
            name="ck_test_runs_status",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_test_runs_exploration_created", "test_runs", ["exploration_id", "created_at"])

    op.create_table(
        "test_run_steps",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("test_run_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("instruction", sa.String(length=1000), nullable=False),
        sa.Column("action", sa.JSON(), nullable=False),
        sa.Column("expected_evidence", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("actual_evidence", sa.JSON(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'passed', 'failed', 'cancelled')",
            name="ck_test_run_steps_status",
        ),
        sa.ForeignKeyConstraint(["test_run_id"], ["test_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("test_run_id", "ordinal", name="uq_test_run_steps_ordinal"),
    )


def downgrade() -> None:
    op.drop_table("test_run_steps")
    op.drop_index("ix_test_runs_exploration_created", table_name="test_runs")
    op.drop_table("test_runs")
