"""GET /reports?reporter= : correspondance exacte, sous RLS."""
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.reports import router
from app.auth.middleware import TenantContext
from app.db.models import Email, Report, Tenant
from app.db.session import get_session


def _client(tenant_id):
    app = FastAPI()
    ctx = TenantContext(user="rep@test", role="tenant_viewer",
                        tenant_ids=(tenant_id,), active_tenant=tenant_id, bypass=False)

    @app.middleware("http")
    async def inject_ctx(request, call_next):
        request.state.tenant = ctx
        return await call_next(request)

    app.include_router(router)
    return TestClient(app)


def test_filtre_reporter():
    with get_session() as db:
        t = Tenant(domain=f"rep-{uuid.uuid4().hex[:8]}.test", name="Rep")
        db.add(t); db.flush()
        em = Email(tenant_id=t.id, message_id=f"m-{uuid.uuid4()}", from_address="x@y.test",
                   subject="s", received_at=datetime.now(timezone.utc),
                   raw_object_key="raw/x.eml", status="parsed_ok")
        db.add(em); db.flush()
        for rep_org in ("google.com", "google.com", "microsoft.com"):
            db.add(Report(tenant_id=t.id, email_id=em.id, source_type="body", status="ok",
                          kind="dmarc", reporter=rep_org, total_units=1, failing_units=0,
                          units_partial=False))
        db.commit()
        tid = str(t.id)
    try:
        c = _client(tid)
        assert c.get("/reports").json()["total"] == 3
        g = c.get("/reports?reporter=google.com").json()
        assert g["total"] == 2
        assert all(it["reporter"] == "google.com" for it in g["items"])
        assert c.get("/reports?reporter=microsoft.com").json()["total"] == 1
    finally:
        with get_session() as db:
            db.query(Report).filter_by(tenant_id=tid).delete()
            db.query(Email).filter_by(tenant_id=tid).delete()
            db.query(Tenant).filter_by(id=tid).delete()
            db.commit()
