"""Lectures /api/v1 : une clé domaine ne voit que son domaine ; une clé plateforme voit tout."""
import uuid
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import public
from app.auth.middleware import TenantMiddleware
from app.db.models import ApiKey, Email, Report, Tenant
from app.db.session import get_session
from app.services import api_keys


@pytest.fixture
def app_client():
    app = FastAPI()
    app.add_middleware(TenantMiddleware)
    app.include_router(public.router)
    return TestClient(app)


@pytest.fixture
def two_tenants_keys():
    made = {}
    with get_session() as db:
        for k in ("a", "b"):
            t = Tenant(domain=f"pub-{k}-{uuid.uuid4().hex[:6]}.test", name=k.upper())
            db.add(t)
            db.flush()
            em = Email(tenant_id=t.id, message_id=f"m-{uuid.uuid4()}",
                       from_address="x@y.test", subject="s",
                       received_at=datetime.now(timezone.utc), raw_object_key="x", status="parsed_ok")
            db.add(em)
            db.flush()
            db.add(Report(tenant_id=t.id, email_id=em.id, source_type="body", status="ok", kind="dmarc"))
            secret, prefix, h = api_keys.generate_key("domain")
            db.add(ApiKey(scope="domain", tenant_id=t.id, prefix=prefix, key_hash=h,
                          label=k, created_by="a@t"))
            made[k] = {"tid": str(t.id), "domain": t.domain, "key": secret, "eid": em.id}
        psecret, pprefix, ph = api_keys.generate_key("platform")
        db.add(ApiKey(scope="platform", prefix=pprefix, key_hash=ph, label="p", created_by="a@t"))
        db.commit()
    made["platform_key"] = psecret
    yield made
    with get_session() as db:
        for k in ("a", "b"):
            tid, eid = made[k]["tid"], made[k]["eid"]
            db.query(ApiKey).filter_by(tenant_id=tid).delete()
            db.query(Report).filter_by(tenant_id=tid).delete()
            db.query(Email).filter_by(id=eid).delete()
            db.query(Tenant).filter_by(id=tid).delete()
        db.query(ApiKey).filter_by(key_hash=api_keys.hash_secret(made["platform_key"])).delete()
        db.commit()


def _auth(secret):
    return {"Authorization": f"Bearer {secret}"}


def test_domain_key_sees_only_its_domain(app_client, two_tenants_keys):
    m = two_tenants_keys
    r = app_client.get("/v1/domains", headers=_auth(m["a"]["key"]))
    assert r.status_code == 200
    domains = [d["domain"] for d in r.json()]
    assert domains == [m["a"]["domain"]]  # uniquement A


def test_platform_key_sees_all_domains(app_client, two_tenants_keys):
    m = two_tenants_keys
    r = app_client.get("/v1/domains", headers=_auth(m["platform_key"]))
    assert r.status_code == 200
    domains = {d["domain"] for d in r.json()}
    assert {m["a"]["domain"], m["b"]["domain"]} <= domains


def test_reports_and_metrics_scoped(app_client, two_tenants_keys):
    m = two_tenants_keys
    assert app_client.get("/v1/reports", headers=_auth(m["a"]["key"])).status_code == 200
    assert app_client.get("/v1/metrics", headers=_auth(m["a"]["key"])).status_code == 200


def test_no_auth_is_401(app_client):
    assert app_client.get("/v1/domains").status_code == 401
