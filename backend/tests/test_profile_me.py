"""GET / PATCH /auth/me : la fiche de l'utilisateur connecte.

PATCH /me ne doit toucher QUE l'appelant (resolu par ctx.user) et JAMAIS role/domaines.
"""
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth.login import router
from app.auth.middleware import TenantContext
from app.auth.passwords import hash_password
from app.db.models import AppUser
from app.db.session import get_session


def _client(email):
    app = FastAPI()
    ctx = TenantContext(user=email, role="tenant_viewer", tenant_ids=(),
                        active_tenant=None, bypass=False)

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


def test_get_me_renvoie_les_champs_profil():
    email = f"me-{uuid.uuid4().hex[:8]}@test.fr"
    _make_user(email)
    try:
        b = _client(email).get("/auth/me").json()
        assert b["email"] == email
        assert b["first_name"] is None
        assert "phone" in b
    finally:
        _cleanup(email)


def test_patch_me_met_a_jour_l_identite():
    email = f"me-{uuid.uuid4().hex[:8]}@test.fr"
    _make_user(email)
    try:
        c = _client(email)
        r = c.patch("/auth/me", json={"email": email, "first_name": "Ada",
                                      "last_name": "Lovelace", "company": "LSI",
                                      "address": "1 rue X", "phone": "0600000000"})
        assert r.status_code == 204
        b = c.get("/auth/me").json()
        assert b["first_name"] == "Ada"
        assert b["company"] == "LSI"
        assert b["phone"] == "0600000000"
    finally:
        _cleanup(email)


def test_patch_me_ignore_role_et_domaines():
    # role/tenant_ids ne sont pas dans le schema -> ignores par FastAPI, le role ne bouge pas.
    email = f"me-{uuid.uuid4().hex[:8]}@test.fr"
    _make_user(email)
    try:
        c = _client(email)
        r = c.patch("/auth/me", json={"email": email, "role": "platform_admin",
                                      "tenant_ids": []})
        assert r.status_code == 204
        with get_session() as db:
            u = db.query(AppUser).filter_by(email=email).first()
            assert u.role == "tenant_viewer"
    finally:
        _cleanup(email)


def test_patch_me_email_deja_pris_409():
    a = f"a-{uuid.uuid4().hex[:8]}@test.fr"
    b_email = f"b-{uuid.uuid4().hex[:8]}@test.fr"
    _make_user(a)
    _make_user(b_email)
    try:
        r = _client(a).patch("/auth/me", json={"email": b_email})
        assert r.status_code == 409
    finally:
        _cleanup(a, b_email)


def test_patch_me_email_mis_en_minuscules():
    email = f"me-{uuid.uuid4().hex[:8]}@test.fr"
    _make_user(email)
    new = email.upper()
    try:
        r = _client(email).patch("/auth/me", json={"email": new})
        assert r.status_code == 204
        with get_session() as db:
            assert db.query(AppUser).filter_by(email=email).first() is not None
    finally:
        _cleanup(email)
