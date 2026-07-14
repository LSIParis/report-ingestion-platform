"""Politique MTA-STS portée par le tenant.

Elle était embarquée dans une image Docker : ajouter un client imposait de modifier le
dépôt, reconstruire l'image et redéployer — l'exact contraire de « nouveau client = zéro
code ». La politique devient une DONNÉE.

`mta_sts_mx` est stocké, jamais recalculé au moment de servir la politique : une panne DNS
transitoire produirait une politique au `mx:` vide ou faux, et en mode `enforce` les
expéditeurs conformes cesseraient de livrer le courrier. Une politique doit être
déterministe.

Revision ID: 0003_mta_sts
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0003_mta_sts"
down_revision = "0002_rls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tenant", sa.Column("mta_sts_mode", sa.Text(), nullable=False,
                                      server_default="none"))
    op.add_column("tenant", sa.Column("mta_sts_max_age", sa.Integer(), nullable=False,
                                      server_default="86400"))
    op.add_column("tenant", sa.Column("mta_sts_mx", JSONB(), nullable=False,
                                      server_default="[]"))
    # Sert à dériver l'`id` publié dans le TXT _mta-sts : cet id DOIT changer à chaque
    # modification de la politique, sinon les expéditeurs gardent l'ancienne en cache
    # jusqu'à expiration de max_age.
    op.add_column("tenant", sa.Column("mta_sts_updated_at", sa.DateTime(timezone=True),
                                      nullable=False, server_default=sa.func.now()))


def downgrade() -> None:
    for col in ("mta_sts_updated_at", "mta_sts_mx", "mta_sts_max_age", "mta_sts_mode"):
        op.drop_column("tenant", col)
