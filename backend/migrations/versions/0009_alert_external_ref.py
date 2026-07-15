"""Reference externe d'une alerte : le numero du ticket ouvert pour elle (canal desk365).

Posee a l ouverture, relue a la fermeture pour annoter le ticket. Nullable : une alerte
non critique, ou ouverte alors que le helpdesk etait injoignable, n a pas de ticket.

Revision ID: 0009_alert_external_ref
"""
import sqlalchemy as sa
from alembic import op

revision = "0009_alert_external_ref"
down_revision = "0008_alert_notified_split"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("alert", sa.Column("external_ref", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("alert", "external_ref")
