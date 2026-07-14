"""Les alertes. Une alerte est un ETAT (ouverte / fermee), pas un message.

Deux garanties viennent de la BASE, pas du code :

 - La RLS : `alert` porte un tenant_id, donc ENABLE + FORCE + policy, comme toute table
   metier (invariant n1). Aucune exception ici — ce sont des donnees de client.

 - L'index unique PARTIEL : une seule alerte OUVERTE par (tenant, kind, dedup_key).
   La deduplication n'est donc pas une convention qu'on espere respectee, c'est une
   contrainte : un bug du reconciliateur produit une erreur, jamais un doublon. Le
   caractere PARTIEL (WHERE closed_at IS NULL) est essentiel : sans lui, une condition
   qui disparait puis revient ne pourrait jamais rouvrir d'alerte.

Revision ID: 0007_alert
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0007_alert"
down_revision = "0006_tls_posture_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "alert",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenant.id"),
                  nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("dedup_key", sa.Text(), nullable=False, server_default=""),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("payload", JSONB(), nullable=False, server_default="{}"),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column("notified_at", sa.DateTime(timezone=True)),
    )

    op.execute("ALTER TABLE alert ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE alert FORCE ROW LEVEL SECURITY;")
    op.execute("""
        CREATE POLICY tenant_isolation ON alert
          USING      (tenant_bypass() OR tenant_id = current_tenant_id())
          WITH CHECK (tenant_bypass() OR tenant_id = current_tenant_id());
    """)
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON alert TO app_api;")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON alert TO app_worker;")

    op.execute("""
        CREATE UNIQUE INDEX ux_alert_ouverte ON alert (tenant_id, kind, dedup_key)
          WHERE closed_at IS NULL;
    """)
    # Sert la page Alertes (les ouvertes d'abord, les plus recentes en tete).
    op.execute("CREATE INDEX ix_alert_ouverture ON alert (tenant_id, opened_at DESC);")


def downgrade() -> None:
    op.drop_table("alert")
