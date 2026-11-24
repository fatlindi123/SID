"""Persist browser explorations, discovered pages, elements and events.

Revision ID: 0002_explorations
Revises: 0001_initial
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0002_explorations"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "explorations",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("start_url", sa.String(length=2048), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("current_url", sa.String(length=2048), nullable=True),
        sa.Column("max_pages", sa.Integer(), nullable=False),
        sa.Column("max_depth", sa.Integer(), nullable=False),
        sa.Column("pages_discovered", sa.Integer(), server_default="0", nullable=False),
        sa.Column("elements_discovered", sa.Integer(), server_default="0", nullable=False),
        sa.Column("event_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed', 'cancelled')",
            name="ck_explorations_status",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_explorations_created_at", "explorations", ["created_at"], unique=False)

    op.create_table(
        "exploration_pages",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("exploration_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("title", sa.String(length=1000), server_default="", nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("depth", sa.Integer(), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("visible_text", sa.Text(), server_default="", nullable=False),
        sa.Column("dom_outline", sa.JSON(), nullable=False),
        sa.Column("screenshot", sa.LargeBinary(), nullable=True),
        sa.Column("screenshot_mime", sa.String(length=80), nullable=True),
        sa.Column("element_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("discovered_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["exploration_id"], ["explorations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("exploration_id", "url", name="uq_exploration_pages_url"),
    )
    op.create_index(
        "ix_exploration_pages_exploration_depth",
        "exploration_pages",
        ["exploration_id", "depth"],
        unique=False,
    )

    op.create_table(
        "observed_elements",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("page_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("tag_name", sa.String(length=40), nullable=False),
        sa.Column("role", sa.String(length=80), nullable=True),
        sa.Column("accessible_name", sa.String(length=1000), nullable=True),
        sa.Column("label", sa.String(length=1000), nullable=True),
        sa.Column("text", sa.String(length=2000), nullable=True),
        sa.Column("selector", sa.String(length=1000), nullable=True),
        sa.Column("attributes", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["page_id"], ["exploration_pages.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_observed_elements_page_kind", "observed_elements", ["page_id", "kind"], unique=False
    )

    op.create_table(
        "exploration_events",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("exploration_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=60), nullable=False),
        sa.Column("message", sa.String(length=1000), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["exploration_id"], ["explorations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("exploration_id", "sequence", name="uq_exploration_events_sequence"),
    )
    op.create_index(
        "ix_exploration_events_exploration_created",
        "exploration_events",
        ["exploration_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_exploration_events_exploration_created", table_name="exploration_events")
    op.drop_table("exploration_events")
    op.drop_index("ix_observed_elements_page_kind", table_name="observed_elements")
    op.drop_table("observed_elements")
    op.drop_index("ix_exploration_pages_exploration_depth", table_name="exploration_pages")
    op.drop_table("exploration_pages")
    op.drop_index("ix_explorations_created_at", table_name="explorations")
    op.drop_table("explorations")
