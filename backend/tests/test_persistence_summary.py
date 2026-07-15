"""persist() pose le resume (summarize) sur le Report, DMARC comme TLS."""
import uuid
from datetime import date, datetime, timezone

from app.db.models import Email, Report, ReportRow, Tenant
from app.db.session import get_session
from app.parsing.base import ParseResult
from app.persistence.service import PersistenceService


def _tenant_email():
    with get_session() as db:
        t = Tenant(domain=f"persist-{uuid.uuid4().hex[:8]}.test", name="Persist")
        db.add(t)
        db.flush()
        em = Email(tenant_id=t.id, message_id=f"m-{uuid.uuid4()}",
                   from_address="x@y.test", subject="s",
                   received_at=datetime.now(timezone.utc), raw_object_key="raw/x.eml",
                   status="parsed_ok")
        db.add(em)
        db.flush()
        db.commit()
        return str(t.id), str(em.id)


def _cleanup(tid, eid):
    # report_row n'a pas de ON DELETE CASCADE vers report (schema 0001) : on doit purger
    # les lignes avant le rapport lui-meme, sinon la FK bloque le DELETE.
    with get_session() as db:
        report_ids = [r.id for r in db.query(Report).filter_by(email_id=eid).all()]
        db.query(ReportRow).filter(ReportRow.report_id.in_(report_ids)).delete(
            synchronize_session=False)
        db.query(Report).filter_by(email_id=eid).delete()
        db.query(Email).filter_by(id=eid).delete()
        db.query(Tenant).filter_by(id=tid).delete()
        db.commit()


def test_persist_dmarc_remplit_le_resume():
    tid, eid = _tenant_email()
    try:
        rows = [
            {"message_count": 100, "aligned": "pass", "reporter": "google.com",
             "report_date": "2026-07-01", "period_end": "2026-07-01"},
            {"message_count": 5, "aligned": "fail", "reporter": "google.com",
             "report_date": "2026-07-01", "period_end": "2026-07-01"},
        ]
        result = ParseResult(status="ok", rows=rows)
        rid = PersistenceService().persist(
            tenant_id=tid, email_id=eid, attachment_id=None, profile_id="_default_dmarc_xml",
            source_type="body", result=result)
        with get_session() as db:
            r = db.get(Report, rid)
            assert r.kind == "dmarc"
            assert r.reporter == "google.com"
            assert r.total_units == 105
            assert r.failing_units == 5
            assert r.units_partial is False
            assert r.period_start == date(2026, 7, 1)
    finally:
        _cleanup(tid, eid)


def test_persist_tls_compte_les_sessions_summary():
    tid, eid = _tenant_email()
    try:
        rows = [
            {"kind": "summary", "successful_sessions": 90, "failed_sessions": 10,
             "reporter": "microsoft.com", "report_date": "2026-07-02",
             "period_end": "2026-07-02"},
            {"kind": "failure", "failure_sessions": 10, "result_type": "certificate-expired",
             "reporter": "microsoft.com"},
        ]
        result = ParseResult(status="ok", rows=rows)
        rid = PersistenceService().persist(
            tenant_id=tid, email_id=eid, attachment_id=None, profile_id="_default_tlsrpt_json",
            source_type="body", result=result)
        with get_session() as db:
            r = db.get(Report, rid)
            assert r.kind == "tls"
            assert r.total_units == 100
            assert r.failing_units == 10
    finally:
        _cleanup(tid, eid)
