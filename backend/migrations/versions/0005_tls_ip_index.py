"""L'IP émettrice d'un échec TLS doit être aussi consultable qu'une IP source DMARC.

Le contrôle d'appartenance de /ip-intel cherche désormais dans les DEUX champs. Sans cet
index, chaque ouverture du panneau sur une IP TLS déclencherait un seq scan sur
report_row.

Revision ID: 0005_tls_ip_index
"""
from alembic import op

revision = "0005_tls_ip_index"
down_revision = "0004_ip_intel"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE INDEX ix_report_row_sending_mta_ip
          ON report_row (tenant_id, (data->>'sending_mta_ip'));
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_report_row_sending_mta_ip;")
