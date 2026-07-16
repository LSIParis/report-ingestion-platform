"""Écriture/lecture réservées à la clé plateforme."""
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import public
from app.auth.middleware import TenantMiddleware
from app.db.models import ApiKey, Tenant, TenantMatchingRule
from app.db.session import get_session
from app.services import api_keys


@pytest.fixture
def app_client():
    app = FastAPI()
    app.add_middleware(TenantMiddleware)
    app.include_router(public.router)
    return TestClient(app)


@pytest.fixture
def keys():
    made = {}
    with get_session() as db:
        s_p, p_p, h_p = api_keys.generate_key("platform")
        db.add(ApiKey(scope="platform", prefix=p_p, key_hash=h_p, label="p", created_by="a@t"))
        t = Tenant(domain=f"dom-{uuid.uuid4().hex[:6]}.test", name="D")
        db.add(t)
        db.flush()
        s_d, p_d, h_d = api_keys.generate_key("domain")
        db.add(ApiKey(scope="domain", tenant_id=t.id, prefix=p_d, key_hash=h_d, label="d", created_by="a@t"))
        db.commit()
        made = {"platform": s_p, "domain": s_d, "tid": str(t.id)}
    yield made
    with get_session() as db:
        db.query(ApiKey).filter_by(key_hash=api_keys.hash_secret(made["platform"])).delete()
        db.query(ApiKey).filter_by(tenant_id=made["tid"]).delete()
        db.query(Tenant).filter_by(id=made["tid"]).delete()
        db.commit()


def _auth(s): return {"Authorization": f"Bearer {s}"}


def test_domain_key_cannot_create_domain(app_client, keys):
    r = app_client.post("/v1/domains", headers=_auth(keys["domain"]),
                        json={"domain": "nope.test"})
    assert r.status_code == 403


def test_domain_key_cannot_read_quarantine(app_client, keys):
    assert app_client.get("/v1/quarantine", headers=_auth(keys["domain"])).status_code == 403


def test_platform_key_creates_domain(app_client, keys):
    d = f"created-{uuid.uuid4().hex[:6]}.test"
    r = app_client.post("/v1/domains", headers=_auth(keys["platform"]), json={"domain": d})
    assert r.status_code == 201 and r.json()["domain"] == d
    created_id = r.json()["id"]
    with get_session() as db:
        db.query(TenantMatchingRule).filter_by(tenant_id=created_id).delete()
        db.query(Tenant).filter_by(id=created_id).delete()
        db.commit()


def test_platform_key_duplicate_domain_409(app_client, keys):
    with get_session() as db:
        dom = db.query(Tenant).filter_by(id=keys["tid"]).one().domain
    r = app_client.post("/v1/domains", headers=_auth(keys["platform"]), json={"domain": dom})
    assert r.status_code == 409


def test_platform_key_reads_quarantine(app_client, keys):
    assert app_client.get("/v1/quarantine", headers=_auth(keys["platform"])).status_code == 200
