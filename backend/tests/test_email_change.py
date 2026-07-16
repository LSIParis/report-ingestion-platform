"""POST /auth/me/email/request + confirm : verification par code (envoi SMTP moque)."""
import re
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.auth.login as login_mod
from app.auth.login import router
from app.auth.middleware import TenantContext
from app.auth.passwords import hash_password
from app.db.models import AppUser
from app.db.session import get_session


@pytest.fixture
def boite(monkeypatch):
    """Capture l'e-mail au lieu de l'envoyer (aucun SMTP reel)."""
    vu = {}

    def faux_envoi(to, subject, body):
        vu["to"] = to
        vu["subject"] = subject
        vu["body"] = body

    monkeypatch.setattr(login_mod, "send_email", faux_envoi)
    return vu


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
        db.commit()


def _cleanup(*emails):
    with get_session() as db:
        for e in emails:
            db.query(AppUser).filter_by(email=e).delete()
        db.commit()


def _code(body):
    return re.search(r"\d{6}", body).group()


def test_request_envoie_code_et_pose_attente(boite):
    old = f"a-{uuid.uuid4().hex[:8]}@test.fr"
    new = f"b-{uuid.uuid4().hex[:8]}@test.fr"
    _make_user(old)
    try:
        r = _client(old).post("/auth/me/email/request", json={"new_email": new})
        assert r.status_code == 202
        assert boite["to"] == new
        assert re.search(r"\d{6}", boite["body"])
        with get_session() as db:
            u = db.query(AppUser).filter_by(email=old).first()
            assert u.pending_email == new       # attente posee
            assert u.email == old                # e-mail de connexion INCHANGE
    finally:
        _cleanup(old, new)


def test_request_meme_email_400(boite):
    old = f"a-{uuid.uuid4().hex[:8]}@test.fr"
    _make_user(old)
    try:
        r = _client(old).post("/auth/me/email/request", json={"new_email": old})
        assert r.status_code == 400
    finally:
        _cleanup(old)


def test_request_email_pris_409(boite):
    old = f"a-{uuid.uuid4().hex[:8]}@test.fr"
    autre = f"c-{uuid.uuid4().hex[:8]}@test.fr"
    _make_user(old)
    _make_user(autre)
    try:
        r = _client(old).post("/auth/me/email/request", json={"new_email": autre})
        assert r.status_code == 409
    finally:
        _cleanup(old, autre)


def test_request_smtp_echec_502(monkeypatch):
    from app.services.mailer import EmailNonEnvoye

    def echoue(*a, **k):
        raise EmailNonEnvoye("smtp ko")

    monkeypatch.setattr(login_mod, "send_email", echoue)
    old = f"a-{uuid.uuid4().hex[:8]}@test.fr"
    new = f"b-{uuid.uuid4().hex[:8]}@test.fr"
    _make_user(old)
    try:
        r = _client(old).post("/auth/me/email/request", json={"new_email": new})
        assert r.status_code == 502
        with get_session() as db:
            u = db.query(AppUser).filter_by(email=old).first()
            assert u.pending_email is None      # rien d orpheline
    finally:
        _cleanup(old, new)


def test_confirm_bon_code_applique(boite):
    old = f"a-{uuid.uuid4().hex[:8]}@test.fr"
    new = f"b-{uuid.uuid4().hex[:8]}@test.fr"
    _make_user(old)
    try:
        c = _client(old)
        c.post("/auth/me/email/request", json={"new_email": new})
        r = c.post("/auth/me/email/confirm", json={"code": _code(boite["body"])})
        assert r.status_code == 204
        with get_session() as db:
            assert db.query(AppUser).filter_by(email=new).first() is not None
            u = db.query(AppUser).filter_by(email=new).first()
            assert u.pending_email is None      # purge
    finally:
        _cleanup(old, new)


def test_confirm_mauvais_code_400_incremente(boite):
    old = f"a-{uuid.uuid4().hex[:8]}@test.fr"
    new = f"b-{uuid.uuid4().hex[:8]}@test.fr"
    _make_user(old)
    try:
        c = _client(old)
        c.post("/auth/me/email/request", json={"new_email": new})
        r = c.post("/auth/me/email/confirm", json={"code": "000000"})
        assert r.status_code == 400
        with get_session() as db:
            u = db.query(AppUser).filter_by(email=old).first()
            assert u.email_code_attempts == 1
            assert u.email == old               # non applique
    finally:
        _cleanup(old, new)


def test_confirm_cinq_essais_429(boite):
    old = f"a-{uuid.uuid4().hex[:8]}@test.fr"
    new = f"b-{uuid.uuid4().hex[:8]}@test.fr"
    _make_user(old)
    try:
        c = _client(old)
        c.post("/auth/me/email/request", json={"new_email": new})
        for _ in range(5):
            c.post("/auth/me/email/confirm", json={"code": "000000"})
        r = c.post("/auth/me/email/confirm", json={"code": _code(boite["body"])})
        assert r.status_code == 429            # meme le bon code est refuse apres 5 essais
    finally:
        _cleanup(old, new)


def test_confirm_expire_400(boite):
    old = f"a-{uuid.uuid4().hex[:8]}@test.fr"
    new = f"b-{uuid.uuid4().hex[:8]}@test.fr"
    _make_user(old)
    try:
        c = _client(old)
        c.post("/auth/me/email/request", json={"new_email": new})
        with get_session() as db:
            u = db.query(AppUser).filter_by(email=old).first()
            u.email_code_expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
            db.commit()
        r = c.post("/auth/me/email/confirm", json={"code": _code(boite["body"])})
        assert r.status_code == 400
    finally:
        _cleanup(old, new)
