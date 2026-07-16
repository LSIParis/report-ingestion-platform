"""Gestion des clés API par un admin : secret rendu une seule fois, jamais relisté."""
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.api_keys_admin import router
from app.auth.middleware import TenantContext
from app.db.models import ApiKey, Tenant
from app.db.session import get_session

ADMIN = "admin-keys@test"


@pytest.fixture
def client():
    app = FastAPI()
    ctx = TenantContext(user=ADMIN, role="platform_admin", tenant_ids=(),
                        active_tenant=None, bypass=True)

    @app.middleware("http")
    async def inject(request, call_next):
        request.state.tenant = ctx
        return await call_next(request)

    app.include_router(router)
    return TestClient(app)


@pytest.fixture
def a_tenant():
    with get_session() as db:
        t = Tenant(domain=f"ak-{uuid.uuid4().hex[:6]}.test", name="AK")
        db.add(t)
        db.commit()
        tid = str(t.id)
    yield tid
    with get_session() as db:
        db.query(ApiKey).filter_by(tenant_id=tid).delete()
        db.query(Tenant).filter_by(id=tid).delete()
        db.commit()


def _cleanup(kid):
    with get_session() as db:
        db.query(ApiKey).filter_by(id=kid).delete()
        db.commit()


def test_create_platform_key_returns_secret_once(client):
    r = client.post("/admin/api-keys", json={"scope": "platform", "label": "etl"})
    assert r.status_code == 201
    body = r.json()
    assert body["secret"].startswith("sk_plat_") and body["scope"] == "platform"
    _cleanup(body["id"])


def test_create_domain_key_requires_tenant(client):
    assert client.post("/admin/api-keys", json={"scope": "domain", "label": "x"}).status_code == 400


def test_create_domain_key(client, a_tenant):
    r = client.post("/admin/api-keys",
                    json={"scope": "domain", "tenant_id": a_tenant, "label": "client"})
    assert r.status_code == 201 and r.json()["secret"].startswith("sk_dom_")


def test_list_never_returns_secret(client):
    r = client.post("/admin/api-keys", json={"scope": "platform", "label": "l"})
    kid = r.json()["id"]
    lst = client.get("/admin/api-keys").json()
    row = next(k for k in lst if k["id"] == kid)
    assert "secret" not in row and "key_hash" not in row and row["prefix"].startswith("sk_plat_")
    _cleanup(kid)


def test_revoke_is_idempotent(client):
    kid = client.post("/admin/api-keys", json={"scope": "platform", "label": "l"}).json()["id"]
    assert client.delete(f"/admin/api-keys/{kid}").status_code == 204
    assert client.delete(f"/admin/api-keys/{kid}").status_code == 204  # idempotent
    row = next(k for k in client.get("/admin/api-keys").json() if k["id"] == kid)
    assert row["revoked_at"] is not None
    _cleanup(kid)
