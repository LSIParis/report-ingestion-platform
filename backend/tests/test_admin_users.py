"""Gestion des comptes (réservée aux administrateurs).

Le risque n'est pas le CRUD, c'est l'auto-verrouillage : un administrateur qui se
supprime ou se rétrograde n'a plus aucun moyen de créer un compte, de lever une
quarantaine, ni de revenir en arrière — il faut repasser par la console du conteneur.
Le serveur refuse donc les deux, quoi que fasse l'interface.
"""
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.admin import router
from app.auth.middleware import TenantContext
from app.auth.passwords import hash_password, verify_password
from app.db.models import AppUser, Tenant, UserTenant
from app.db.session import get_session

ADMIN = "admin-test@lsi.test"


@pytest.fixture
def env():
    """Un admin (l'appelant), un lecteur, deux domaines."""
    with get_session() as db:
        t1 = Tenant(domain=f"x-{uuid.uuid4().hex[:6]}.test", name="X")
        t2 = Tenant(domain=f"y-{uuid.uuid4().hex[:6]}.test", name="Y")
        db.add_all([t1, t2])
        db.flush()
        admin = AppUser(email=ADMIN, role="platform_admin",
                        password_hash=hash_password("admin-initial-1234"))
        viewer = AppUser(email=f"v-{uuid.uuid4().hex[:6]}@lsi.test", role="tenant_viewer",
                         password_hash=hash_password("viewer-initial-1234"))
        db.add_all([admin, viewer])
        db.flush()
        db.add(UserTenant(user_id=viewer.id, tenant_id=t1.id))
        db.commit()
        data = {"admin_id": str(admin.id), "viewer_id": str(viewer.id),
                "viewer_email": viewer.email, "t1": str(t1.id), "t2": str(t2.id)}

    yield data

    with get_session() as db:
        db.query(UserTenant).filter(
            UserTenant.user_id.in_([data["admin_id"], data["viewer_id"]])).delete(
            synchronize_session=False)
        db.query(AppUser).filter(
            AppUser.id.in_([data["admin_id"], data["viewer_id"]])).delete(
            synchronize_session=False)
        db.query(Tenant).filter(Tenant.id.in_([data["t1"], data["t2"]])).delete(
            synchronize_session=False)
        db.commit()


@pytest.fixture
def client():
    """On injecte le contexte tenant comme le fait le vrai middleware, plutôt que de
    surcharger les dépendances : `require_role` est une *factory* (elle produit une
    nouvelle fonction à chaque appel), donc un dependency_overrides ne remplacerait pas
    celle que le routeur a capturée à l'import. Ici, `get_tenant_ctx` ET `require_role`
    lisent tous deux request.state.tenant, comme en production."""
    app = FastAPI()
    ctx = TenantContext(user=ADMIN, role="platform_admin", tenant_ids=(),
                        active_tenant=None, bypass=True)

    @app.middleware("http")
    async def inject_ctx(request, call_next):
        request.state.tenant = ctx
        return await call_next(request)

    app.include_router(router)
    return TestClient(app)


# ------------------------------------------------- anti-verrouillage (le cœur)
def test_un_admin_ne_peut_pas_se_supprimer(client, env):
    r = client.delete(f"/admin/users/{env['admin_id']}")
    assert r.status_code == 409

    with get_session() as db:
        assert db.get(AppUser, env["admin_id"]) is not None


def test_un_admin_ne_peut_pas_se_retrograder(client, env):
    r = client.patch(f"/admin/users/{env['admin_id']}",
                     json={"role": "tenant_viewer", "tenant_ids": [env["t1"]]})
    assert r.status_code == 409

    with get_session() as db:
        assert db.get(AppUser, env["admin_id"]).role == "platform_admin"


# ------------------------------------------------- création
def test_creation_et_rattachement(client, env):
    email = f"n-{uuid.uuid4().hex[:6]}@lsi.test"
    r = client.post("/admin/users", json={
        "email": email.upper(), "role": "tenant_viewer",
        "password": "un-mot-de-passe-solide", "tenant_ids": [env["t1"], env["t2"]]})
    assert r.status_code == 201
    body = r.json()
    assert body["email"] == email          # normalisé en minuscules
    assert len(body["tenants"]) == 2

    with get_session() as db:
        u = db.query(AppUser).filter_by(email=email).first()
        assert verify_password("un-mot-de-passe-solide", u.password_hash)
        db.query(UserTenant).filter_by(user_id=u.id).delete()
        db.delete(u)
        db.commit()


def test_lecteur_sans_domaine_refuse(client):
    """Un compte en lecture sans domaine reçoit 403 à chaque appel : mort-né."""
    r = client.post("/admin/users", json={
        "email": "vide@lsi.test", "role": "tenant_viewer",
        "password": "un-mot-de-passe-solide", "tenant_ids": []})
    assert r.status_code == 422


def test_email_deja_pris(client, env):
    r = client.post("/admin/users", json={
        "email": env["viewer_email"], "role": "tenant_viewer",
        "password": "un-mot-de-passe-solide", "tenant_ids": [env["t1"]]})
    assert r.status_code == 409


def test_mot_de_passe_trop_court_refuse(client, env):
    r = client.post("/admin/users", json={
        "email": "court@lsi.test", "role": "tenant_viewer",
        "password": "court", "tenant_ids": [env["t1"]]})
    assert r.status_code == 422


# ------------------------------------------------- modification
def test_remplacement_des_domaines(client, env):
    r = client.patch(f"/admin/users/{env['viewer_id']}", json={"tenant_ids": [env["t2"]]})
    assert r.status_code == 200
    assert [t["id"] for t in r.json()["tenants"]] == [env["t2"]]   # t1 retiré


def test_retirer_le_dernier_domaine_dun_lecteur_est_refuse(client, env):
    r = client.patch(f"/admin/users/{env['viewer_id']}", json={"tenant_ids": []})
    assert r.status_code == 422

    with get_session() as db:      # l'accès existant est intact
        assert db.query(UserTenant).filter_by(user_id=env["viewer_id"]).count() == 1


def test_reinitialisation_du_mot_de_passe(client, env):
    r = client.post(f"/admin/users/{env['viewer_id']}/password",
                    json={"new_password": "remis-a-zero-par-admin"})
    assert r.status_code == 204

    with get_session() as db:
        u = db.get(AppUser, env["viewer_id"])
        assert verify_password("remis-a-zero-par-admin", u.password_hash)


def test_suppression_dun_autre_compte(client, env):
    assert client.delete(f"/admin/users/{env['viewer_id']}").status_code == 204
    with get_session() as db:
        assert db.get(AppUser, env["viewer_id"]) is None
        assert db.query(UserTenant).filter_by(user_id=env["viewer_id"]).count() == 0
