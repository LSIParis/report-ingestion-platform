"""etat "changement d'e-mail en attente" sur app_user

Revision ID: 0012_email_verification
Revises: 0011_user_profile
"""
import sqlalchemy as sa
from alembic import op

revision = "0012_email_verification"
down_revision = "0011_user_profile"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("app_user", sa.Column("pending_email", sa.Text(), nullable=True))
    op.add_column("app_user", sa.Column("email_code_hash", sa.Text(), nullable=True))
    op.add_column("app_user", sa.Column("email_code_expires_at",
                                        sa.DateTime(timezone=True), nullable=True))
    op.add_column("app_user", sa.Column("email_code_attempts", sa.Integer(),
                                        nullable=False, server_default=sa.text("0")))


def downgrade() -> None:
    for col in ("email_code_attempts", "email_code_expires_at", "email_code_hash",
                "pending_email"):
        op.drop_column("app_user", col)
