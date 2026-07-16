"""PATCH /admin/users/{id} : l'admin edite l'identite + l'e-mail d'un compte tiers."""
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.admin import router
from app.auth.middleware import TenantContext
from app.auth.passwords import hash_password
from app.db.models import AppUser
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


def _make_user(email):
    with get_session() as db:
        u = AppUser(email=email, role="tenant_viewer", password_hash=hash_password("x" * 12))
        db.add(u)
        db.flush()
        uid = str(u.id)
        db.commit()
    return uid


def _cleanup(*emails):
    with get_session() as db:
        for e in emails:
            db.query(AppUser).filter_by(email=e).delete()
        db.commit()


def test_admin_met_a_jour_identite_et_email():
    admin = f"admin-{uuid.uuid4().hex[:8]}@test.fr"
    target = f"t-{uuid.uuid4().hex[:8]}@test.fr"
    new_email = f"t2-{uuid.uuid4().hex[:8]}@test.fr"
    _make_user(admin)
    uid = _make_user(target)
    try:
        r = _admin_client(admin).patch(f"/admin/users/{uid}", json={
            "email": new_email, "first_name": "Grace", "company": "LSI"})
        assert r.status_code == 200
        body = r.json()
        assert body["email"] == new_email
        assert body["first_name"] == "Grace"
        assert body["company"] == "LSI"
    finally:
        _cleanup(admin, target, new_email)


def test_admin_email_deja_pris_409():
    admin = f"admin-{uuid.uuid4().hex[:8]}@test.fr"
    a = f"a-{uuid.uuid4().hex[:8]}@test.fr"
    b = f"b-{uuid.uuid4().hex[:8]}@test.fr"
    _make_user(admin)
    uid_a = _make_user(a)
    _make_user(b)
    try:
        r = _admin_client(admin).patch(f"/admin/users/{uid_a}", json={"email": b})
        assert r.status_code == 409
    finally:
        _cleanup(admin, a, b)
