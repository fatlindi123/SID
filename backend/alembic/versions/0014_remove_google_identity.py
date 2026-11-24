"""remove unused Google identity storage"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0014_remove_google_identity"
down_revision: Union[str, None] = "0013_first_login_onboarding"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # No user rows are linked to Google. Keep historical OAuth challenge rows
    # intact while removing the unused identity column and its unique index.
    op.drop_index("uq_users_google_sub", table_name="users")
    op.drop_column("users", "google_sub")


def downgrade() -> None:
    op.add_column("users", sa.Column("google_sub", sa.String(length=255), nullable=True))
    op.create_index("uq_users_google_sub", "users", ["google_sub"], unique=True)
