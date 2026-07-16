"""fiche d'identite sur app_user (nom, prenom, societe, adresse, telephone)

Revision ID: 0011_user_profile
Revises: 0010_report_summary
"""
import sqlalchemy as sa
from alembic import op

revision = "0011_user_profile"
down_revision = "0010_report_summary"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for col in ("first_name", "last_name", "company", "address", "phone"):
        op.add_column("app_user", sa.Column(col, sa.Text(), nullable=True))


def downgrade() -> None:
    for col in ("phone", "address", "company", "last_name", "first_name"):
        op.drop_column("app_user", col)
