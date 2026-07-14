"""Index pour tls_posture.posture() : sans lui, chaque ouverture du panneau MTA-STS
balaie TOUT report_row du tenant, lignes DMARC comprises.

`posture()` filtre sur deux expressions JSON : `data->>'kind' IN ('summary',
'failure')` et `data->>'report_date' >= cutoff`. Aucun index existant ne les couvre --
meme l'index natif `idx_reportrow_tenant_date` (migration 0001) ne s'applique pas ici :
il porte sur la COLONNE `report_date`, pas sur l'expression JSON `data->>'report_date'`
que cette requete utilise reellement (le service lit `ReportRow.data["report_date"]`,
pas `ReportRow.report_date`).

Un seul index compose porte les deux predicats de la requete, dans l'ordre ou ils
sont appliques : `kind` d'abord (IN sur seulement 2 valeurs, quasi une egalite),
`report_date` ensuite (inegalite, beneficie d'un btree). Meme forme que 0004/0005
(index d'expression JSON, `tenant_id` en tete pour rester coherent avec la RLS).

Revision ID: 0006_tls_posture_index
"""
from alembic import op

revision = "0006_tls_posture_index"
down_revision = "0005_tls_ip_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE INDEX ix_report_row_kind_date
          ON report_row (tenant_id, (data->>'kind'), (data->>'report_date'));
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_report_row_kind_date;")
