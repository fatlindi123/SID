"""persist safe interaction observations"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0005_interaction_observations"
down_revision: Union[str, None] = "0004_test_execution"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "interaction_observations",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("exploration_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("page_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("element_ordinal", sa.Integer(), nullable=False),
        sa.Column("probe_key", sa.String(length=64), nullable=False),
        sa.Column("action_type", sa.String(length=20), nullable=False),
        sa.Column("outcome", sa.String(length=20), nullable=False),
        sa.Column("reason", sa.String(length=160), nullable=True),
        sa.Column("before_evidence", sa.JSON(), nullable=False),
        sa.Column("after_evidence", sa.JSON(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "outcome IN ('observed', 'skipped', 'blocked', 'failed')",
            name="ck_interaction_observations_outcome",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "exploration_id", "page_id", "probe_key",
            name="uq_interaction_observations_probe",
        ),
    )
    op.create_index(
        "ix_interaction_observations_exploration_page",
        "interaction_observations",
        ["exploration_id", "page_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_interaction_observations_exploration_page",
        table_name="interaction_observations",
    )
    op.drop_table("interaction_observations")
