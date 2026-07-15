"""GET /reports/{id}/breakdown : agregats par rapport, sous RLS.

Les casts JSONB sont partages avec metrics.py ; on verifie ici qu'ils sont bien
FILTRES sur ce report_id (pas sur toute la base) et que l'isolation renvoie 404.
"""
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.reports import router
from app.auth.middleware import TenantContext
from app.db.models import Email, Report, ReportRow, Tenant
from app.db.session import get_session


def _client(tenant_id):
    app = FastAPI()
    ctx = TenantContext(user="brk@test", role="tenant_viewer",
                        tenant_ids=(tenant_id,), active_tenant=tenant_id, bypass=False)

    @app.middleware("http")
    async def inject_ctx(request, call_next):
        request.state.tenant = ctx
        return await call_next(request)

    app.include_router(router)
    return TestClient(app)


def _dmarc_row(tid, rid, ip, count, aligned, dkim, spf):
    return ReportRow(tenant_id=tid, report_id=rid, report_date=None,
                     data={"source_ip": ip, "message_count": count, "aligned": aligned,
                           "dkim": dkim, "spf": spf, "policy_domain": "exemple.fr",
                           "reporter": "google.com"})


def _setup_dmarc():
    with get_session() as db:
        t = Tenant(domain=f"brk-{uuid.uuid4().hex[:8]}.test", name="Brk")
        db.add(t)
        db.flush()
        em = Email(tenant_id=t.id, message_id=f"m-{uuid.uuid4()}", from_address="x@y.test",
                   subject="s", received_at=datetime.now(timezone.utc),
                   raw_object_key="raw/x.eml", status="parsed_ok")
        db.add(em)
        db.flush()
        rep = Report(tenant_id=t.id, email_id=em.id, source_type="attachment", status="ok",
                     kind="dmarc", reporter="google.com", total_units=610, failing_units=110,
                     units_partial=False)
        db.add(rep)
        db.flush()
        # 1.1.1.1 : 400 alignes (dkim pass) + 100 non alignes ; 2.2.2.2 : 110 non alignes.
        db.add_all([
            _dmarc_row(t.id, rep.id, "1.1.1.1", 400, "pass", "pass", "fail"),
            _dmarc_row(t.id, rep.id, "1.1.1.1", 100, "fail", "fail", "fail"),
            _dmarc_row(t.id, rep.id, "2.2.2.2", 110, "fail", "fail", "fail"),
        ])
        db.commit()
        return str(t.id), str(rep.id)


def _cleanup(tid):
    with get_session() as db:
        rids = [r.id for r in db.query(Report.id).filter_by(tenant_id=tid).all()]
        db.query(ReportRow).filter(ReportRow.report_id.in_(rids)).delete(synchronize_session=False)
        db.query(Report).filter_by(tenant_id=tid).delete()
        db.query(Email).filter_by(tenant_id=tid).delete()
        db.query(Tenant).filter_by(id=tid).delete()
        db.commit()


def test_breakdown_dmarc_agrege_par_ip_et_dkim_spf():
    tid, rid = _setup_dmarc()
    try:
        b = _client(tid).get(f"/reports/{rid}/breakdown").json()
        assert b["policy_domain"] == "exemple.fr"
        assert b["dkim_aligned"] == 400        # seule la ligne dkim=pass
        assert b["spf_aligned"] == 0
        srcs = {s["source_ip"]: s for s in b["sources"]}
        assert srcs["1.1.1.1"]["messages"] == 500
        assert srcs["1.1.1.1"]["compliant"] == 400
        assert srcs["1.1.1.1"]["failing"] == 100
        assert srcs["2.2.2.2"]["messages"] == 110
        assert srcs["2.2.2.2"]["failing"] == 110
        # trie par volume decroissant
        assert b["sources"][0]["source_ip"] == "1.1.1.1"
    finally:
        _cleanup(tid)


def test_breakdown_tls_domaine_seul():
    with get_session() as db:
        t = Tenant(domain=f"brk-{uuid.uuid4().hex[:8]}.test", name="BrkTls")
        db.add(t)
        db.flush()
        em = Email(tenant_id=t.id, message_id=f"m-{uuid.uuid4()}", from_address="x@y.test",
                   subject="s", received_at=datetime.now(timezone.utc),
                   raw_object_key="raw/x.eml", status="parsed_ok")
        db.add(em)
        db.flush()
        rep = Report(tenant_id=t.id, email_id=em.id, source_type="attachment", status="ok",
                     kind="tls", reporter="microsoft.com", total_units=100, failing_units=0,
                     units_partial=False)
        db.add(rep)
        db.flush()
        db.add(ReportRow(tenant_id=t.id, report_id=rep.id, report_date=None,
                         data={"kind": "summary", "policy_domain": "exemple.fr",
                               "successful_sessions": 100, "failed_sessions": 0,
                               "reporter": "microsoft.com"}))
        db.commit()
        tid, rid = str(t.id), str(rep.id)
    try:
        b = _client(tid).get(f"/reports/{rid}/breakdown").json()
        assert b["policy_domain"] == "exemple.fr"
        assert "sources" not in b          # TLS : pas d'agregat DMARC
        assert "dkim_aligned" not in b
    finally:
        _cleanup(tid)


def test_breakdown_autre_tenant_404():
    tid, rid = _setup_dmarc()
    other = None
    try:
        with get_session() as db:
            t2 = Tenant(domain=f"brk-{uuid.uuid4().hex[:8]}.test", name="Autre")
            db.add(t2)
            db.flush()
            other = str(t2.id)
            db.commit()
        # Le client scope sur `other` ne doit PAS voir le rapport de `tid`.
        assert _client(other).get(f"/reports/{rid}/breakdown").status_code == 404
    finally:
        _cleanup(tid)
        if other:
            with get_session() as db:
                db.query(Tenant).filter_by(id=other).delete()
                db.commit()
