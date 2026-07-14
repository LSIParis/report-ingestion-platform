"""La page Alertes. Sans elle, une alerte fermée ne laisse aucune trace consultable, et le
webhook devient la seule mémoire du système.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.admin import router
from app.auth.middleware import TenantContext
from app.db.models import Alert, Tenant
from app.db.session import get_session


@pytest.fixture
def alertes():
    now = datetime.now(timezone.utc)
    with get_session() as db:
        t = Tenant(domain=f"api-{uuid.uuid4().hex[:8]}.test", name="Api")
        db.add(t)
        db.flush()
        db.add(Alert(tenant_id=t.id, kind="never_reported", dedup_key="",
                     severity="critical", payload={"domain": t.domain}, opened_at=now))
        db.add(Alert(tenant_id=t.id, kind="tls_failure", dedup_key="vieille",
                     severity="warning", payload={}, opened_at=now - timedelta(days=3),
                     closed_at=now - timedelta(days=1)))
        db.commit()
        tid, domaine = str(t.id), t.domain

    yield domaine

    with get_session() as db:
        db.query(Alert).filter_by(tenant_id=tid).delete()
        db.query(Tenant).filter_by(id=tid).delete()
        db.commit()


@pytest.fixture
def client():
    app = FastAPI()
    ctx = TenantContext(user="admin@platform.io", role="platform_admin",
                        tenant_ids=(), active_tenant=None, bypass=True)

    @app.middleware("http")
    async def inject(request, call_next):
        request.state.tenant = ctx
        return await call_next(request)

    app.include_router(router)
    return TestClient(app)


def test_les_alertes_ouvertes_par_defaut(client, alertes):
    items = client.get("/admin/alerts").json()

    miennes = [a for a in items if a["domain"] == alertes]
    assert [a["kind"] for a in miennes] == ["never_reported"]   # la fermée n'y est pas
    assert miennes[0]["severity"] == "critical"
    assert miennes[0]["closed_at"] is None


def test_status_all_montre_aussi_les_fermees(client, alertes):
    items = client.get("/admin/alerts?status=all").json()

    miennes = [a for a in items if a["domain"] == alertes]
    kinds = {a["kind"] for a in miennes}
    assert kinds == {"never_reported", "tls_failure"}


def test_le_domaine_accompagne_chaque_alerte(client, alertes):
    """Une alerte sans domaine est inexploitable : c'est la première chose qu'on lit."""
    items = client.get("/admin/alerts?status=all").json()
    assert all(a["domain"] for a in items)
