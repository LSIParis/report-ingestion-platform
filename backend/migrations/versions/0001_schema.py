"""Schéma initial"""
from alembic import op

revision = "0001_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")
    op.execute("""
      CREATE TABLE tenant (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        domain text NOT NULL UNIQUE,
        name text NOT NULL,
        status text NOT NULL DEFAULT 'active',
        created_at timestamptz NOT NULL DEFAULT now()
      );
      CREATE TABLE app_user (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        email text NOT NULL UNIQUE,
        password_hash text NOT NULL,
        role text NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now()
      );
      CREATE TABLE user_tenant (
        user_id uuid REFERENCES app_user(id),
        tenant_id uuid REFERENCES tenant(id),
        PRIMARY KEY (user_id, tenant_id)
      );
      CREATE TABLE tenant_matching_rule (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL REFERENCES tenant(id),
        rule_type text NOT NULL,
        pattern text NOT NULL,
        priority int NOT NULL DEFAULT 100,
        is_active boolean DEFAULT true
      );
      CREATE TABLE email (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid REFERENCES tenant(id),
        message_id text NOT NULL UNIQUE,
        from_address text NOT NULL,
        subject text NOT NULL,
        received_at timestamptz NOT NULL,
        raw_object_key text NOT NULL,
        status text NOT NULL DEFAULT 'received',
        resolved_by text,
        created_at timestamptz NOT NULL DEFAULT now()
      );
      CREATE TABLE attachment (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid REFERENCES tenant(id),
        email_id uuid NOT NULL REFERENCES email(id),
        filename text NOT NULL,
        mime_type text,
        format text,
        object_key text NOT NULL,
        size_bytes bigint
      );
      CREATE TABLE report (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL REFERENCES tenant(id),
        email_id uuid NOT NULL REFERENCES email(id),
        attachment_id uuid REFERENCES attachment(id),
        profile_id text,
        source_type text NOT NULL,
        status text NOT NULL,
        row_count int DEFAULT 0,
        parsed_at timestamptz,
        created_at timestamptz NOT NULL DEFAULT now()
      );
      CREATE TABLE report_row (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL REFERENCES tenant(id),
        report_id uuid NOT NULL REFERENCES report(id),
        report_date date,
        data jsonb NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now()
      );
      CREATE TABLE parsing_error (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid REFERENCES tenant(id),
        email_id uuid REFERENCES email(id),
        report_id uuid REFERENCES report(id),
        severity text NOT NULL,
        code text NOT NULL,
        message text NOT NULL,
        context jsonb,
        created_at timestamptz NOT NULL DEFAULT now()
      );
      CREATE TABLE audit_log (
        id bigserial PRIMARY KEY,
        tenant_id uuid,
        actor text NOT NULL,
        action text NOT NULL,
        target_type text,
        target_id uuid,
        metadata jsonb,
        created_at timestamptz NOT NULL DEFAULT now()
      );

      CREATE INDEX idx_email_tenant_received ON email(tenant_id, received_at DESC);
      CREATE INDEX idx_report_tenant_status  ON report(tenant_id, status);
      CREATE INDEX idx_reportrow_tenant_date ON report_row(tenant_id, report_date);
      CREATE INDEX idx_reportrow_data_gin    ON report_row USING gin(data);
    """)


def downgrade() -> None:
    for t in ["audit_log", "parsing_error", "report_row", "report", "attachment",
              "email", "tenant_matching_rule", "user_tenant", "app_user", "tenant"]:
        op.execute(f"DROP TABLE IF EXISTS {t} CASCADE;")
