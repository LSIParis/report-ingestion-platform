"""Résolution d'une clé API et construction du contexte au middleware."""
import uuid

import pytest

from app.auth.middleware import TenantMiddleware
from app.db.models import ApiKey, Tenant
from app.db.session import get_session
from app.services import api_keys


class FakeURL:
    def __init__(self, path): self.path = path


class FakeRequest:
    def __init__(self, path, headers=None):
        self.url = FakeURL(path)
        self.headers = headers or {}


@pytest.fixture
def platform_key():
    secret, prefix, h = api_keys.generate_key("platform")
    with get_session() as db:
        k = ApiKey(scope="platform", prefix=prefix, key_hash=h, label="t", created_by="a@t")
        db.add(k)
        db.commit()
        kid = k.id
    yield secret
    with get_session() as db:
        db.query(ApiKey).filter_by(id=kid).delete()
        db.commit()


@pytest.fixture
def domain_key():
    with get_session() as db:
        t = Tenant(domain=f"k-{uuid.uuid4().hex[:8]}.test", name="K")
        db.add(t)
        db.flush()
        secret, prefix, h = api_keys.generate_key("domain")
        k = ApiKey(scope="domain", tenant_id=t.id, prefix=prefix, key_hash=h, label="t", created_by="a@t")
        db.add(k)
        db.commit()
        tid, kid = str(t.id), k.id
    yield secret, tid
    with get_session() as db:
        db.query(ApiKey).filter_by(id=kid).delete()
        db.query(Tenant).filter_by(id=tid).delete()
        db.commit()


def test_generate_and_hash_roundtrip():
    secret, prefix, h = api_keys.generate_key("domain")
    assert secret.startswith("sk_dom_") and prefix == secret[:14]
    assert api_keys.hash_secret(secret) == h and len(h) == 64


def test_resolve_unknown_returns_none():
    assert api_keys.resolve("sk_dom_" + "x" * 40) is None


def test_platform_context_is_bypass(platform_key):
    ctx = TenantMiddleware._build_api_key_context(FakeRequest("/v1/domains"), platform_key)
    assert ctx.api_key_scope == "platform" and ctx.bypass and ctx.role == "platform_admin"


def test_domain_context_is_scoped(domain_key):
    secret, tid = domain_key
    ctx = TenantMiddleware._build_api_key_context(FakeRequest("/v1/reports"), secret)
    assert ctx.api_key_scope == "domain" and not ctx.bypass
    assert ctx.active_tenant == tid and ctx.tenant_ids == (tid,)


def test_platform_key_honors_x_tenant_id(platform_key):
    ctx = TenantMiddleware._build_api_key_context(
        FakeRequest("/v1/reports", {"X-Tenant-Id": "abc"}), platform_key)
    assert ctx.api_key_scope == "platform" and ctx.active_tenant == "abc" and not ctx.bypass


def test_api_key_blocked_outside_api_v1(domain_key):
    secret, _ = domain_key
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as e:
        TenantMiddleware._build_api_key_context(FakeRequest("/admin/tenants"), secret)
    assert e.value.status_code == 403


def test_revoked_key_returns_none(domain_key):
    secret, _ = domain_key
    from datetime import datetime, timezone
    with get_session() as db:
        db.query(ApiKey).filter_by(key_hash=api_keys.hash_secret(secret)).update(
            {"revoked_at": datetime.now(timezone.utc)})
        db.commit()
    assert api_keys.resolve(secret) is None
