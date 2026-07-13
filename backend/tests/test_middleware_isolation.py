"""Frontière d'autorisation : le middleware tenant, avec de vrais JWT signés.

C'est LE contrôle qui empêche un utilisateur du domaine X d'atteindre les données du
domaine Y (invariant §5 : X-Tenant-Id doit être ⊂ des tenant_ids du jeton signé). La RLS
est le filet en dessous ; ici on vérifie le premier verrou.

Testé aussi : le refus doit être un **403**, pas un 500. Une HTTPException levée dans un
BaseHTTPMiddleware échappe aux gestionnaires de FastAPI et ressort en 500 — observé en
production. Un refus d'isolation qui se présente comme une erreur serveur est
indistinguable d'un bug, et le front ne peut pas le traiter.
"""
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.auth.middleware import TenantMiddleware
from app.config import settings

TENANT_A = "11111111-1111-1111-1111-111111111111"
TENANT_B = "22222222-2222-2222-2222-222222222222"


@pytest.fixture(scope="module")
def keys():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()).decode()
    pub = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    return priv, pub


@pytest.fixture(scope="module")
def client(keys):
    priv, pub = keys
    object.__setattr__(settings, "jwt_private_key", priv)
    object.__setattr__(settings, "jwt_public_key", pub)

    app = FastAPI()
    app.add_middleware(TenantMiddleware)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/reports")
    def reports(request: Request):
        ctx = request.state.tenant
        return {"active_tenant": ctx.active_tenant, "bypass": ctx.bypass}

    return TestClient(app)


def token(keys, *, role="tenant_viewer", tenant_ids=(TENANT_A,)):
    priv, _ = keys
    now = datetime.now(timezone.utc)
    return jwt.encode({
        "sub": "user@a.tld", "role": role, "tenant_ids": list(tenant_ids),
        "iss": settings.jwt_issuer, "aud": settings.jwt_audience,
        "iat": now, "exp": now + timedelta(minutes=5),
    }, priv, algorithm="RS256")


def auth(tok, tenant=None):
    h = {"Authorization": f"Bearer {tok}"}
    if tenant:
        h["X-Tenant-Id"] = tenant
    return h


# ---------- le cœur ----------
def test_tenant_hors_du_jeton_est_refuse_en_403(client, keys):
    """Un viewer de A demande explicitement les données de B."""
    r = client.get("/reports", headers=auth(token(keys), tenant=TENANT_B))
    assert r.status_code == 403, "un refus d'isolation ne doit JAMAIS être un 500"
    assert "Tenant non autorisé" in r.json()["detail"]


def test_son_propre_tenant_passe(client, keys):
    r = client.get("/reports", headers=auth(token(keys), tenant=TENANT_A))
    assert r.status_code == 200
    assert r.json() == {"active_tenant": TENANT_A, "bypass": False}


def test_tenant_implicite_si_un_seul_rattachement(client, keys):
    r = client.get("/reports", headers=auth(token(keys)))
    assert r.json()["active_tenant"] == TENANT_A


def test_multi_tenant_exige_un_choix_explicite(client, keys):
    r = client.get("/reports", headers=auth(token(keys, tenant_ids=(TENANT_A, TENANT_B))))
    assert r.status_code == 400          # ambigu → on refuse, on ne choisit pas


# ---------- authentification ----------
def test_sans_jeton_401(client):
    assert client.get("/reports").status_code == 401


def test_jeton_signe_par_une_autre_cle_401(client, keys):
    autre = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = autre.private_bytes(serialization.Encoding.PEM,
                             serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption()).decode()
    now = datetime.now(timezone.utc)
    forge = jwt.encode({"sub": "x", "role": "platform_admin", "tenant_ids": [TENANT_B],
                        "iss": settings.jwt_issuer, "aud": settings.jwt_audience,
                        "iat": now, "exp": now + timedelta(minutes=5)},
                       pem, algorithm="RS256")
    assert client.get("/reports", headers=auth(forge, TENANT_B)).status_code == 401


def test_jeton_expire_401(client, keys):
    priv, _ = keys
    past = datetime.now(timezone.utc) - timedelta(hours=2)
    old = jwt.encode({"sub": "x", "role": "tenant_viewer", "tenant_ids": [TENANT_A],
                      "iss": settings.jwt_issuer, "aud": settings.jwt_audience,
                      "iat": past, "exp": past + timedelta(minutes=5)},
                     priv, algorithm="RS256")
    assert client.get("/reports", headers=auth(old, TENANT_A)).status_code == 401


def test_sans_aucun_tenant_rattache_403(client, keys):
    r = client.get("/reports", headers=auth(token(keys, tenant_ids=())))
    assert r.status_code == 403


# ---------- admin ----------
def test_platform_admin_sans_entete_passe_en_bypass(client, keys):
    r = client.get("/reports", headers=auth(token(keys, role="platform_admin",
                                                  tenant_ids=(TENANT_A, TENANT_B))))
    assert r.json() == {"active_tenant": None, "bypass": True}


def test_health_reste_public(client):
    assert client.get("/health").status_code == 200
