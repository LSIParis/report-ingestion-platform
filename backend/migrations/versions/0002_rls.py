"""RLS multitenant : rôles, helpers, policies"""
from alembic import op

revision = "0002_rls"
down_revision = "0001_schema"
branch_labels = None
depends_on = None

TENANT_TABLES = ["email", "attachment", "report", "report_row", "parsing_error"]


def upgrade() -> None:
    # Rôles (idempotent : init-roles.sql les crée déjà avec LOGIN en docker)
    op.execute("""
        DO $$
        BEGIN
          IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_api') THEN
            CREATE ROLE app_api NOLOGIN;
          END IF;
          IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_worker') THEN
            CREATE ROLE app_worker NOLOGIN BYPASSRLS;
          END IF;
        END $$;
    """)

    op.execute("""
        CREATE OR REPLACE FUNCTION current_tenant_id() RETURNS uuid
        LANGUAGE sql STABLE AS $$
          SELECT NULLIF(current_setting('app.current_tenant', true), '')::uuid
        $$;
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION tenant_bypass() RETURNS boolean
        LANGUAGE sql STABLE AS $$
          SELECT COALESCE(current_setting('app.bypass_tenant', true), 'off') = 'on'
        $$;
    """)

    for tbl in TENANT_TABLES:
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {tbl} TO app_api;")
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {tbl} TO app_worker;")
        op.execute(f"ALTER TABLE {tbl} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {tbl} FORCE ROW LEVEL SECURITY;")
        op.execute(f"""
            CREATE POLICY tenant_isolation ON {tbl}
              USING      (tenant_bypass() OR tenant_id = current_tenant_id())
              WITH CHECK (tenant_bypass() OR tenant_id = current_tenant_id());
        """)

    # Tables non-tenant nécessaires à l'API/worker (pas de RLS) : GRANT explicites
    for tbl in ["tenant", "app_user", "user_tenant", "tenant_matching_rule", "audit_log"]:
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {tbl} TO app_api;")
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {tbl} TO app_worker;")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO app_api, app_worker;")


def downgrade() -> None:
    for tbl in TENANT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {tbl};")
        op.execute(f"ALTER TABLE {tbl} NO FORCE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {tbl} DISABLE ROW LEVEL SECURITY;")
    op.execute("DROP FUNCTION IF EXISTS tenant_bypass();")
    op.execute("DROP FUNCTION IF EXISTS current_tenant_id();")
