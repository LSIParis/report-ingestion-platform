"""Durcissements API publique v1 :

1. require_role refuse tout principal « clé API » (defense en profondeur : une cle est
   deja bornee a /v1 par le middleware, mais une route protegee par require_role ne doit
   JAMAIS accepter une cle, meme si la garde de perimetre regressait).
2. delete_tenant d'un domaine pristine portant une cle par-domaine reussit (pas de 500
   sur la FK api_key.tenant_id) et supprime la cle.
"""
import uuid

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.api.admin import router as admin_router
from app.auth.deps import require_role
from app.auth.middleware import TenantContext
from app.db.models import ApiKey, Tenant, TenantMatchingRule
from app.db.session import get_session
from app.services import api_keys

ADMIN = "admin-hardening@test"


# --------------------------------------------------------------- Durcissement 1
def test_require_role_refuse_une_cle_api():
    app = FastAPI()

    @app.get("/protege", dependencies=[Depends(require_role("platform_admin"))])
    def protege():
        return {"ok": True}

    # Contexte « clé API plateforme » ayant pourtant le role platform_admin : require_role
    # doit refuser sur le seul critere api_key_scope (defense en profondeur).
    ctx = TenantContext(user="apikey:sk_plat_x", role="platform_admin", tenant_ids=(),
                        active_tenant=None, bypass=True, api_key_scope="platform")

    @app.middleware("http")
    async def inject(request, call_next):
        request.state.tenant = ctx
        return await call_next(request)

    assert TestClient(app).get("/protege").status_code == 403


def test_require_role_accepte_un_admin_jwt():
    app = FastAPI()

    @app.get("/protege", dependencies=[Depends(require_role("platform_admin"))])
    def protege():
        return {"ok": True}

    ctx = TenantContext(user=ADMIN, role="platform_admin", tenant_ids=(),
                        active_tenant=None, bypass=True)  # api_key_scope=None par defaut

    @app.middleware("http")
    async def inject(request, call_next):
        request.state.tenant = ctx
        return await call_next(request)

    assert TestClient(app).get("/protege").status_code == 200


# --------------------------------------------------------------- Durcissement 2
@pytest.fixture
def admin_client():
    app = FastAPI()
    ctx = TenantContext(user=ADMIN, role="platform_admin", tenant_ids=(),
                        active_tenant=None, bypass=True)

    @app.middleware("http")
    async def inject(request, call_next):
        request.state.tenant = ctx
        return await call_next(request)

    app.include_router(admin_router)
    return TestClient(app)


def test_delete_tenant_supprime_ses_cles_api(admin_client):
    # Domaine pristine (aucun e-mail collecte) portant une cle par-domaine.
    with get_session() as db:
        t = Tenant(domain=f"del-{uuid.uuid4().hex[:8]}.test", name="Del")
        db.add(t)
        db.flush()
        _, prefix, h = api_keys.generate_key("domain")
        db.add(ApiKey(scope="domain", tenant_id=t.id, prefix=prefix, key_hash=h,
                      label="dom", created_by=ADMIN))
        db.commit()
        tid = str(t.id)
        key_hash = h

    try:
        r = admin_client.delete(f"/admin/tenants/{tid}")
        assert r.status_code == 204  # pas de 500 sur la FK
        with get_session() as db:
            assert db.get(Tenant, tid) is None
            assert db.query(ApiKey).filter_by(key_hash=key_hash).first() is None
    finally:
        with get_session() as db:
            db.query(ApiKey).filter_by(key_hash=key_hash).delete()
            db.query(TenantMatchingRule).filter_by(tenant_id=tid).delete()
            db.query(Tenant).filter_by(id=tid).delete()
            db.commit()
