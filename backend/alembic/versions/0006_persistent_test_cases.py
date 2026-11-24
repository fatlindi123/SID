"""persist stable, versioned test cases and link runs to their case"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0006_persistent_test_cases"
down_revision: Union[str, None] = "0005_interaction_observations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "test_cases",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("exploration_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("identity_hash", sa.String(length=64), nullable=False),
        sa.Column("source_analysis_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("analysis_version_hash", sa.String(length=64), nullable=True),
        sa.Column("scenario_version_hash", sa.String(length=64), nullable=False),
        sa.Column("definition", sa.JSON(), nullable=False),
        sa.Column("is_current", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source IN ('ai_generated', 'user_created')",
            name="ck_test_cases_source",
        ),
        sa.CheckConstraint(
            "(source = 'ai_generated' AND source_analysis_id IS NOT NULL AND analysis_version_hash IS NOT NULL) "
            "OR (source = 'user_created' AND source_analysis_id IS NULL AND analysis_version_hash IS NULL)",
            name="ck_test_cases_source_analysis",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("exploration_id", "source", "identity_hash", name="uq_test_cases_identity"),
    )
    op.create_index(
        "ix_test_cases_exploration_current",
        "test_cases",
        ["exploration_id", "is_current"],
    )
    # The role creating this table owns it and can grant/refer to it. The existing
    # exploration and analysis tables intentionally have no foreign keys here.
    with op.batch_alter_table("test_runs") as batch:
        batch.add_column(sa.Column("test_case_id", sa.Uuid(as_uuid=True), nullable=True))
        batch.create_foreign_key(
            "fk_test_runs_test_case_id_test_cases",
            "test_cases",
            ["test_case_id"],
            ["id"],
            ondelete="RESTRICT",
        )
    op.create_index("ix_test_runs_test_case_id", "test_runs", ["test_case_id"])


def downgrade() -> None:
    op.drop_index("ix_test_runs_test_case_id", table_name="test_runs")
    with op.batch_alter_table("test_runs") as batch:
        batch.drop_constraint("fk_test_runs_test_case_id_test_cases", type_="foreignkey")
        batch.drop_column("test_case_id")
    op.drop_index("ix_test_cases_exploration_current", table_name="test_cases")
    op.drop_table("test_cases")
