"""Compte connecté : /auth/me et changement de mot de passe.

Deux points sensibles :
 - /auth/me doit dériver la liste des domaines des `tenant_ids` du JETON SIGNÉ, jamais
   d'un en-tête de requête — sinon il suffirait de forger l'en-tête pour se découvrir
   des domaines qu'on n'a pas.
 - Le changement de mot de passe doit exiger le mot de passe actuel : avec un jeton volé
   (XSS, poste laissé ouvert), on pourrait sinon verrouiller le compte de sa victime.
"""
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth.deps import get_tenant_ctx
from app.auth.login import router
from app.auth.middleware import TenantContext
from app.auth.passwords import hash_password, verify_password
from app.db.models import AppUser, Tenant, UserTenant
from app.db.session import get_session

PW = "mot-de-passe-initial-123"


@pytest.fixture
def account():
    """Un utilisateur rattaché à UN domaine, alors que DEUX existent."""
    with get_session() as db:
        mine = Tenant(domain=f"a-{uuid.uuid4().hex[:6]}.test", name="A Mien")
        other = Tenant(domain=f"b-{uuid.uuid4().hex[:6]}.test", name="B Autre")
        db.add_all([mine, other])
        db.flush()
        u = AppUser(email=f"u-{uuid.uuid4().hex[:6]}@test.tld", role="tenant_viewer",
                    password_hash=hash_password(PW))
        db.add(u)
        db.flush()
        db.add(UserTenant(user_id=u.id, tenant_id=mine.id))
        db.commit()
        data = (str(u.id), u.email, str(mine.id), mine.domain, str(other.id), other.domain)

    yield data

    with get_session() as db:
        uid, _, mid, _, oid, _ = data
        db.query(UserTenant).filter_by(user_id=uid).delete()
        db.query(AppUser).filter_by(id=uid).delete()
        db.query(Tenant).filter(Tenant.id.in_([mid, oid])).delete(synchronize_session=False)
        db.commit()


def make_client(email, role, tenant_ids):
    """Monte les routes avec un contexte tenant injecté — l'équivalent d'un JWT validé."""
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_tenant_ctx] = lambda: TenantContext(
        user=email, role=role, tenant_ids=tuple(tenant_ids),
        active_tenant=tenant_ids[0] if tenant_ids else None, bypass=False)
    return TestClient(app)


# ---------------- /auth/me ----------------
def test_me_ne_liste_que_les_domaines_du_jeton(account):
    _, email, mid, mdomain, _, odomain = account
    r = make_client(email, "tenant_viewer", [mid]).get("/auth/me").json()

    assert r["email"] == email
    domains = [t["domain"] for t in r["tenants"]]
    assert domains == [mdomain]
    assert odomain not in domains        # le domaine d'autrui reste invisible


def test_admin_voit_tous_les_domaines(account):
    _, email, mid, mdomain, _, odomain = account
    r = make_client(email, "platform_admin", [mid]).get("/auth/me").json()
    domains = [t["domain"] for t in r["tenants"]]
    assert mdomain in domains and odomain in domains


# ---------------- changement de mot de passe ----------------
def test_changement_exige_le_mot_de_passe_actuel(account):
    _, email, mid, _, _, _ = account
    c = make_client(email, "tenant_viewer", [mid])

    r = c.post("/auth/password", json={"current_password": "faux",
                                       "new_password": "un-nouveau-mot-de-passe"})
    assert r.status_code == 403

    with get_session() as db:                       # rien n'a changé en base
        u = db.query(AppUser).filter_by(email=email).first()
        assert verify_password(PW, u.password_hash)


def test_changement_reussi(account):
    _, email, mid, _, _, _ = account
    c = make_client(email, "tenant_viewer", [mid])

    r = c.post("/auth/password", json={"current_password": PW,
                                       "new_password": "un-nouveau-mot-de-passe"})
    assert r.status_code == 204

    with get_session() as db:
        u = db.query(AppUser).filter_by(email=email).first()
        assert verify_password("un-nouveau-mot-de-passe", u.password_hash)
        assert not verify_password(PW, u.password_hash)


def test_mot_de_passe_trop_court_refuse(account):
    _, email, mid, _, _, _ = account
    c = make_client(email, "tenant_viewer", [mid])
    r = c.post("/auth/password", json={"current_password": PW, "new_password": "court"})
    assert r.status_code == 422


def test_mot_de_passe_au_dela_de_72_octets_refuse(account):
    """Limite dure de bcrypt : au-delà, le secret serait tronqué en silence, et deux
    mots de passe partageant leurs 72 premiers octets deviendraient interchangeables."""
    _, email, mid, _, _, _ = account
    c = make_client(email, "tenant_viewer", [mid])
    r = c.post("/auth/password", json={"current_password": PW, "new_password": "a" * 73})
    assert r.status_code == 422
