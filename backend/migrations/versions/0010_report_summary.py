"""report summary denormalise (cycle 1) : colonnes + backfill via summarize()

Revision ID: 0010_report_summary
Revises: 0009_alert_external_ref
"""
import sqlalchemy as sa
from alembic import op

revision = "0010_report_summary"
down_revision = "0009_alert_external_ref"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1) Colonnes. `kind` d'abord nullable : on ne peut la remplir qu'apres le backfill.
    op.add_column("report", sa.Column("kind", sa.Text(), nullable=True))
    op.add_column("report", sa.Column("reporter", sa.Text(), nullable=True))
    op.add_column("report", sa.Column("total_units", sa.Integer(), nullable=True))
    op.add_column("report", sa.Column("failing_units", sa.Integer(), nullable=True))
    op.add_column("report", sa.Column("units_partial", sa.Boolean(),
                                      nullable=False, server_default=sa.text("false")))
    op.add_column("report", sa.Column("period_start", sa.Date(), nullable=True))
    op.add_column("report", sa.Column("period_end", sa.Date(), nullable=True))

    # 2) Backfill : recalcule chaque rapport avec la MEME fonction que l'ingestion.
    # Import differe (dans upgrade) : evite tout effet de bord a l'import du module de migration.
    from app.persistence.summary import summarize

    bind = op.get_bind()
    report_ids = [row[0] for row in bind.execute(sa.text("SELECT id FROM report"))]
    upd = sa.text(
        "UPDATE report SET kind=:kind, reporter=:reporter, total_units=:total, "
        "failing_units=:failing, units_partial=:partial, period_start=:pstart, "
        "period_end=:pend WHERE id=:rid"
    )
    for rid in report_ids:
        # psycopg rend le JSONB `data` deja sous forme de dict.
        data_rows = [r[0] for r in bind.execute(
            sa.text("SELECT data FROM report_row WHERE report_id = :rid"), {"rid": rid})]
        s = summarize(data_rows)
        bind.execute(upd, {"kind": s.kind, "reporter": s.reporter, "total": s.total_units,
                           "failing": s.failing_units, "partial": s.units_partial,
                           "pstart": s.period_start, "pend": s.period_end, "rid": rid})

    # 3) Tous les rapports ont desormais un kind : on peut le rendre NOT NULL.
    op.alter_column("report", "kind", nullable=False)


def downgrade() -> None:
    for col in ("period_end", "period_start", "units_partial", "failing_units",
                "total_units", "reporter", "kind"):
        op.drop_column("report", col)
