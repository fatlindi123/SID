"""Persist one AI analysis per browser exploration."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0003_exploration_analyses"
down_revision: Union[str, None] = "0002_explorations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "exploration_analyses",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("exploration_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="1", nullable=False),
        sa.Column("model", sa.String(length=200), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed')",
            name="ck_exploration_analyses_status",
        ),
        sa.ForeignKeyConstraint(["exploration_id"], ["explorations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("exploration_id", name="uq_exploration_analyses_exploration_id"),
    )


def downgrade() -> None:
    op.drop_table("exploration_analyses")
