"""Cache des faits DNS sur une IP, et l'index qui rend le panneau instantané.

`ip_intel` n'a PAS de tenant_id et n'a PAS de RLS — comme `tenant` ou `audit_log` en
0002 : ce sont des faits publics sur Internet, pas des données de client. Ce qui empêche
un client de sonder l'existence d'une IP chez un autre n'est pas une policy sur cette
table, c'est le contrôle d'appartenance de la route : elle exige que l'IP apparaisse dans
une ligne de rapport visible SOUS RLS avant de lire le cache. IP jamais vue → 404.

L'index sur report_row sert les deux usages de la route : le contrôle d'appartenance et
le résumé d'activité. Sans lui, chaque ouverture du panneau déclenche un seq scan.

Revision ID: 0004_ip_intel
"""
import sqlalchemy as sa
from alembic import op

revision = "0004_ip_intel"
down_revision = "0003_mta_sts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ip_intel",
        sa.Column("ip", sa.Text(), primary_key=True),
        sa.Column("ptr", sa.Text()),
        sa.Column("fcrdns", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("asn", sa.Integer()),
        sa.Column("as_org", sa.Text()),
        sa.Column("country", sa.Text()),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )

    # Table non-tenant : GRANT explicites, pas de RLS (même traitement qu'en 0002).
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ip_intel TO app_api;")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ip_intel TO app_worker;")

    op.execute("""
        CREATE INDEX ix_report_row_source_ip
          ON report_row (tenant_id, (data->>'source_ip'));
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_report_row_source_ip;")
    op.drop_table("ip_intel")
