"""add authentication records and owner-scoped workspace data"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0012_authentication"
down_revision: Union[str, None] = "0011_reports"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, None] = None

def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("full_name", sa.String(length=160), nullable=False),
        sa.Column("password_hash", sa.String(length=512), nullable=True),
        sa.Column("google_sub", sa.String(length=255), nullable=True),
        sa.Column("email_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("length(email) BETWEEN 3 AND 320", name="ck_users_email_length"),
        sa.CheckConstraint("length(full_name) BETWEEN 1 AND 160", name="ck_users_name_length"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_index("uq_users_google_sub", "users", ["google_sub"], unique=True)
    op.create_table(
        "auth_sessions",
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("csrf_hash", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("token_hash"),
    )
    op.create_index("ix_auth_sessions_user_expiry", "auth_sessions", ["user_id", "expires_at"])
    op.create_table(
        "auth_challenges",
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("nonce_hash", sa.String(length=64), nullable=True),
        sa.Column("redirect_to", sa.String(length=2048), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("kind IN ('email_verification', 'password_reset', 'google_oauth')", name="ck_auth_challenges_kind"),
        sa.PrimaryKeyConstraint("token_hash"),
    )
    op.create_index("ix_auth_challenges_user_kind", "auth_challenges", ["user_id", "kind"])
    op.create_table(
        "auth_bootstrap",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("claimed_by", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute(sa.text("INSERT INTO auth_bootstrap (id, claimed_by, claimed_at) VALUES (1, NULL, NULL)"))
    op.create_table(
        "workspace_record_owners",
        sa.Column("resource_type", sa.String(length=64), nullable=False),
        sa.Column("resource_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("owner_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("resource_type", "resource_id"),
    )
    op.create_index("ix_workspace_record_owners_owner_type", "workspace_record_owners", ["owner_id", "resource_type"])


def downgrade() -> None:
    op.drop_index("ix_workspace_record_owners_owner_type", table_name="workspace_record_owners")
    op.drop_table("workspace_record_owners")
    op.drop_table("auth_bootstrap")
    op.drop_index("ix_auth_challenges_user_kind", table_name="auth_challenges")
    op.drop_table("auth_challenges")
    op.drop_index("ix_auth_sessions_user_expiry", table_name="auth_sessions")
    op.drop_table("auth_sessions")
    op.drop_index("uq_users_google_sub", table_name="users")
    op.drop_table("users")
