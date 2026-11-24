"""add editable metadata, soft deletion, and independent user case runs"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0007_test_case_management"
down_revision: Union[str, None] = "0006_persistent_test_cases"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("test_cases", sa.Column("name", sa.String(length=200), nullable=True))
    op.add_column("test_cases", sa.Column("description", sa.Text(), nullable=True))
    op.add_column("test_cases", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))

    test_cases = sa.table(
        "test_cases",
        sa.column("id", sa.Uuid(as_uuid=True)),
        sa.column("definition", sa.JSON()),
        sa.column("name", sa.String(length=200)),
        sa.column("description", sa.Text()),
    )
    connection = op.get_bind()
    for row in connection.execute(sa.select(test_cases.c.id, test_cases.c.definition)).mappings():
        definition = row["definition"] if isinstance(row["definition"], dict) else {}
        scenario = definition.get("scenario") if isinstance(definition.get("scenario"), dict) else {}
        name = str(scenario.get("title") or "Untitled test case").strip()[:200] or "Untitled test case"
        description = str(scenario.get("objective") or "").strip()[:4000]
        connection.execute(
            test_cases.update()
            .where(test_cases.c.id == row["id"])
            .values(name=name, description=description)
        )

    with op.batch_alter_table("test_cases") as batch:
        batch.alter_column(
            "name",
            existing_type=sa.String(length=200),
            nullable=False,
        )
        batch.alter_column(
            "description",
            existing_type=sa.Text(),
            nullable=False,
            server_default=sa.text("''"),
        )
    op.create_index(
        "ix_test_cases_exploration_name",
        "test_cases",
        ["exploration_id", "name"],
    )

    with op.batch_alter_table("test_runs") as batch:
        batch.alter_column(
            "analysis_id",
            existing_type=sa.Uuid(as_uuid=True),
            nullable=True,
        )


def downgrade() -> None:
    connection = op.get_bind()
    test_runs = sa.table(
        "test_runs",
        sa.column("analysis_id", sa.Uuid(as_uuid=True)),
    )
    null_analysis_runs = connection.scalar(
        sa.select(sa.func.count()).select_from(test_runs).where(test_runs.c.analysis_id.is_(None))
    )
    if null_analysis_runs:
        raise RuntimeError(
            "Cannot downgrade: test runs without analysis associations exist. Preserve those runs first."
        )

    with op.batch_alter_table("test_runs") as batch:
        batch.alter_column(
            "analysis_id",
            existing_type=sa.Uuid(as_uuid=True),
            nullable=False,
        )
    op.drop_index("ix_test_cases_exploration_name", table_name="test_cases")
    with op.batch_alter_table("test_cases") as batch:
        batch.drop_column("deleted_at")
        batch.drop_column("description")
        batch.drop_column("name")
