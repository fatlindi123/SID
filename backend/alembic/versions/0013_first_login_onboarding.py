"""persist first-login onboarding completion"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0013_first_login_onboarding"
down_revision: Union[str, None] = "0012_authentication"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("onboarding_completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Accounts that existed before the welcome page was introduced are treated
    # as returning users. New accounts keep a NULL value until they continue.
    op.execute(sa.text(
        "UPDATE users SET onboarding_completed_at = created_at "
        "WHERE onboarding_completed_at IS NULL"
    ))


def downgrade() -> None:
    op.drop_column("users", "onboarding_completed_at")
