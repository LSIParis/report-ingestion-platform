"""PATCH /admin/tenants/{id} met a jour alert_email ; la liste le renvoie."""
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.admin import router
from app.auth.middleware import TenantContext
from app.db.models import Tenant
from app.db.session import get_session


def _admin_client(admin_email):
    app = FastAPI()
    ctx = TenantContext(user=admin_email, role="platform_admin", tenant_ids=(),
                        active_tenant=None, bypass=True)

    @app.middleware("http")
    async def inject_ctx(request, call_next):
        request.state.tenant = ctx
        return await call_next(request)

    app.include_router(router)
    return TestClient(app)


def _make_tenant():
    with get_session() as db:
        t = Tenant(domain=f"al-{uuid.uuid4().hex[:8]}.test", name="Al")
        db.add(t)
        db.flush()
        tid = str(t.id)
        db.commit()
    return tid


def _cleanup(tid):
    with get_session() as db:
        db.query(Tenant).filter_by(id=tid).delete()
        db.commit()


def test_patch_alert_email_et_liste():
    tid = _make_tenant()
    try:
        c = _admin_client("admin@test.fr")
        r = c.patch(f"/admin/tenants/{tid}", json={"alert_email": "ops@exemple.fr, sec@exemple.fr"})
        assert r.status_code == 200
        assert r.json()["alert_email"] == "ops@exemple.fr, sec@exemple.fr"
        liste = c.get("/admin/tenants").json()
        ligne = next(x for x in liste if x["id"] == tid)
        assert ligne["alert_email"] == "ops@exemple.fr, sec@exemple.fr"
    finally:
        _cleanup(tid)


def test_patch_alert_email_vide_efface():
    tid = _make_tenant()
    try:
        c = _admin_client("admin@test.fr")
        c.patch(f"/admin/tenants/{tid}", json={"alert_email": "ops@exemple.fr"})
        r = c.patch(f"/admin/tenants/{tid}", json={"alert_email": ""})
        assert r.status_code == 200
        assert r.json()["alert_email"] is None
    finally:
        _cleanup(tid)


def test_patch_alert_email_invalide_422():
    tid = _make_tenant()
    try:
        c = _admin_client("admin@test.fr")
        r = c.patch(f"/admin/tenants/{tid}", json={"alert_email": "pasunemail"})
        assert r.status_code == 422
    finally:
        _cleanup(tid)
