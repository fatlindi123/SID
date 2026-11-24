"""add project metadata and exploration assignments"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0009_projects_management"
down_revision: Union[str, None] = "0008_evidence_knowledge_graph"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Sidecar tables avoid ALTERing legacy tables owned by a different PostgreSQL role.
    # Cross-table integrity is enforced by the project service; exploration_id is the
    # primary key so an exploration can be assigned to at most one project.
    op.create_table(
        "project_metadata",
        sa.Column("project_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("environment", sa.String(length=100), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("project_id"),
    )
    op.create_table(
        "project_explorations",
        sa.Column("exploration_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("project_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("exploration_id"),
    )
    op.create_index(
        "ix_project_explorations_project",
        "project_explorations",
        ["project_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_project_explorations_project", table_name="project_explorations")
    op.drop_table("project_explorations")
    op.drop_table("project_metadata")
