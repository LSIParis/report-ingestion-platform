"""destinataire d'alerte e-mail par tenant (tenant.alert_email)

Revision ID: 0013_tenant_alert_email
Revises: 0012_email_verification
"""
import sqlalchemy as sa
from alembic import op

revision = "0013_tenant_alert_email"
down_revision = "0012_email_verification"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tenant", sa.Column("alert_email", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("tenant", "alert_email")
