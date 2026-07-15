"""GET /reports?kind= filtre par type, sous RLS (aucun WHERE tenant_id applicatif)."""
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.reports import router
from app.auth.middleware import TenantContext
from app.db.models import Email, Report, Tenant
from app.db.session import get_session


def _client(tenant_id):
    # Meme montage que test_metrics_dmarc.py / test_ip_intel_api.py : un VRAI contexte
    # tenant injecte dans request.state.tenant (RLS active, bypass=False). get_db lit ce
    # contexte et pose SET LOCAL app.current_tenant. NE PAS court-circuiter get_db par une
    # get_session() (plan worker BYPASSRLS) : le test verrait alors toute la base.
    app = FastAPI()
    ctx = TenantContext(user="kind-test@example.test", role="tenant_viewer",
                        tenant_ids=(tenant_id,), active_tenant=tenant_id, bypass=False)

    @app.middleware("http")
    async def inject_ctx(request, call_next):
        request.state.tenant = ctx
        return await call_next(request)

    app.include_router(router)
    return TestClient(app)


def test_filtre_kind():
    with get_session() as db:
        t = Tenant(domain=f"kind-{uuid.uuid4().hex[:8]}.test", name="Kind")
        db.add(t)
        db.flush()
        em = Email(tenant_id=t.id, message_id=f"m-{uuid.uuid4()}", from_address="x@y.test",
                   subject="s", received_at=datetime.now(timezone.utc),
                   raw_object_key="raw/x.eml", status="parsed_ok")
        db.add(em)
        db.flush()
        db.add(Report(tenant_id=t.id, email_id=em.id, source_type="body", status="ok",
                      kind="dmarc", reporter="google.com", total_units=10, failing_units=1,
                      units_partial=False))
        db.add(Report(tenant_id=t.id, email_id=em.id, source_type="body", status="ok",
                      kind="tls", reporter="microsoft.com", total_units=5, failing_units=0,
                      units_partial=False))
        db.commit()
        tid = str(t.id)

    try:
        c = _client(tid)
        tous = c.get("/reports").json()
        assert tous["total"] == 2
        dmarc = c.get("/reports?kind=dmarc").json()
        assert dmarc["total"] == 1
        assert dmarc["items"][0]["kind"] == "dmarc"
        assert dmarc["items"][0]["reporter"] == "google.com"
        assert dmarc["items"][0]["total_units"] == 10
        tls = c.get("/reports?kind=tls").json()
        assert tls["total"] == 1
        assert tls["items"][0]["kind"] == "tls"
    finally:
        with get_session() as db:
            db.query(Report).filter_by(tenant_id=tid).delete()
            db.query(Email).filter_by(tenant_id=tid).delete()
            db.query(Tenant).filter_by(id=tid).delete()
            db.commit()
