from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import text

from app.db.models import ParsingError, Report, ReportRow
from app.db.session import get_session
from app.parsing.base import ParseResult


class PersistenceService:
    """Écrit report + rows + erreurs dans le contexte tenant.

    NB : le worker utilise le rôle BYPASSRLS, mais on pose quand même
    app.current_tenant par cohérence et pour rester exact si l'on bascule
    ce chemin sur le plan restreint.
    """

    def persist(self, *, tenant_id: str, email_id: str, attachment_id: str | None,
                profile_id: str | None, source_type: str, result: ParseResult) -> str:
        with get_session() as session:
            session.execute(text("SELECT set_config('app.current_tenant', :tid, true)"),
                            {"tid": str(tenant_id)})

            report = Report(
                tenant_id=tenant_id, email_id=email_id, attachment_id=attachment_id,
                profile_id=profile_id, source_type=source_type,
                status=result.status, row_count=len(result.rows),
                parsed_at=datetime.now(timezone.utc),
            )
            session.add(report)
            session.flush()

            session.bulk_save_objects([
                ReportRow(tenant_id=tenant_id, report_id=report.id,
                          report_date=r.get("report_date"), data=r)
                for r in result.rows
            ])
            session.bulk_save_objects([
                ParsingError(tenant_id=tenant_id, email_id=email_id, report_id=report.id,
                             severity=e.get("severity", "error"), code=e["code"],
                             message=e["message"],
                             context={"row_index": e.get("row_index"),
                                      "field": e.get("field")})
                for e in result.errors
            ])
            session.commit()
            return str(report.id)
