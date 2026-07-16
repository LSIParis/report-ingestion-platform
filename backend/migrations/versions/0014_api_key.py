"""table api_key (cles API plateforme + par-domaine) — table d'auth, SANS RLS

Revision ID: 0014_api_key
Revises: 0013_tenant_alert_email
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0014_api_key"
down_revision = "0013_tenant_alert_email"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_key",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", pg.UUID(as_uuid=True), sa.ForeignKey("tenant.id"), nullable=True),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("prefix", sa.Text(), nullable=False),
        sa.Column("key_hash", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("key_hash", name="uq_api_key_hash"),
        # scope='platform' <=> tenant_id NULL. L'egalite de deux booleens est valide en PG.
        sa.CheckConstraint("(scope = 'platform') = (tenant_id IS NULL)",
                           name="ck_api_key_scope_tenant"),
    )
    op.create_index("ix_api_key_tenant_id", "api_key", ["tenant_id"])
    # api_key est une table d'AUTH (comme app_user) : PAS de RLS. GRANT explicites.
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON api_key TO app_api;")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON api_key TO app_worker;")


def downgrade() -> None:
    op.drop_index("ix_api_key_tenant_id", table_name="api_key")
    op.drop_table("api_key")
