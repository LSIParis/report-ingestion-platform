"""Gestion des domaines surveillés.

Deux points sensibles :
 - la suppression ne doit JAMAIS pouvoir effacer l'historique d'un client (rapports,
   lignes, pièces jointes). Un domaine qui a collecté quoi que ce soit se suspend, il
   ne se supprime pas ;
 - suspendre doit réellement couper la collecte. Un « suspendu » qui continue à se voir
   attribuer les nouveaux rapports ne veut rien dire.
"""
import uuid
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.admin import router
from app.auth.middleware import TenantContext
from app.db.models import Email, Report, Tenant, TenantMatchingRule
from app.db.session import get_session
from app.services.tenants import dmarc_subject_pattern

ADMIN = "admin-dom@lsi.test"


@pytest.fixture
def client():
    app = FastAPI()
    ctx = TenantContext(user=ADMIN, role="platform_admin", tenant_ids=(),
                        active_tenant=None, bypass=True)

    @app.middleware("http")
    async def inject_ctx(request, call_next):
        request.state.tenant = ctx
        return await call_next(request)

    app.include_router(router)
    return TestClient(app)


@pytest.fixture
def cleanup():
    created: list[str] = []
    yield created
    with get_session() as db:
        for tid in created:
            rep_ids = [r.id for r in db.query(Report.id).filter_by(tenant_id=tid).all()]
            db.query(Report).filter(Report.id.in_(rep_ids)).delete(synchronize_session=False)
            db.query(Email).filter_by(tenant_id=tid).delete()
            db.query(TenantMatchingRule).filter_by(tenant_id=tid).delete()
            db.query(Tenant).filter_by(id=tid).delete()
        db.commit()


def _domain() -> str:
    return f"d-{uuid.uuid4().hex[:8]}.test"


# ------------------------------------------------------------------- création
def test_creation_pose_la_regle_dmarc(client, cleanup):
    d = _domain()
    r = client.post("/admin/tenants", json={"domain": d.upper(), "name": "Client"})
    assert r.status_code == 201
    tid = r.json()["id"]
    cleanup.append(tid)
    assert r.json()["domain"] == d          # normalisé en minuscules

    with get_session() as db:
        rules = db.query(TenantMatchingRule).filter_by(tenant_id=tid).all()
        assert len(rules) == 1
        assert rules[0].rule_type == "subject_regex"      # et JAMAIS 'sender'
        assert rules[0].pattern == dmarc_subject_pattern(d)
        assert rules[0].is_active


def test_domaine_invalide_refuse(client):
    for bad in ("pas-un-domaine", "dmarc@client.com", "", "http://client.com"):
        assert client.post("/admin/tenants", json={"domain": bad}).status_code == 422


def test_domaine_deja_surveille(client, cleanup):
    d = _domain()
    cleanup.append(client.post("/admin/tenants", json={"domain": d}).json()["id"])
    assert client.post("/admin/tenants", json={"domain": d}).status_code == 409


# ------------------------------------------------------------------ suspension
def test_suspendre_desactive_les_regles_de_resolution(client, cleanup):
    """Sinon le pipeline continuerait à attribuer les nouveaux rapports au domaine
    suspendu, et « suspendu » ne voudrait rien dire."""
    d = _domain()
    tid = client.post("/admin/tenants", json={"domain": d}).json()["id"]
    cleanup.append(tid)

    assert client.patch(f"/admin/tenants/{tid}", json={"active": False}).json()["status"] == "suspended"
    with get_session() as db:
        assert not any(r.is_active for r in
                       db.query(TenantMatchingRule).filter_by(tenant_id=tid).all())

    assert client.patch(f"/admin/tenants/{tid}", json={"active": True}).json()["status"] == "active"
    with get_session() as db:
        assert all(r.is_active for r in
                   db.query(TenantMatchingRule).filter_by(tenant_id=tid).all())


# ------------------------------------------------------- suppression (le cœur)
def test_suppression_dun_domaine_vierge(client, cleanup):
    d = _domain()
    tid = client.post("/admin/tenants", json={"domain": d}).json()["id"]

    assert client.delete(f"/admin/tenants/{tid}").status_code == 204
    with get_session() as db:
        assert db.get(Tenant, tid) is None
        assert db.query(TenantMatchingRule).filter_by(tenant_id=tid).count() == 0


def test_procedure_refusee_si_la_boite_de_collecte_n_est_pas_configuree(
        client, cleanup, monkeypatch):
    """Sans COLLECTION_MAILBOX, les contrôles interrogeraient des noms tronqués et
    diraient « à faire » pour des enregistrements corrects. Une liste de contrôle qui
    ment est pire qu'aucune liste : on refuse de la produire.

    Bug réellement rencontré : IMAP_USER n'était passé qu'au conteneur imap-worker,
    pas à l'API — la procédure signalait comme manquantes des autorisations posées.
    """
    from app.config import settings
    monkeypatch.setattr(settings, "collection_mailbox", "", raising=False)

    tid = client.post("/admin/tenants", json={"domain": _domain()}).json()["id"]
    cleanup.append(tid)

    r = client.get(f"/admin/tenants/{tid}/onboarding")
    assert r.status_code == 503
    assert "COLLECTION_MAILBOX" in r.json()["detail"]


def test_un_domaine_qui_a_collecte_ne_peut_pas_etre_supprime(client, cleanup):
    """Le supprimer effacerait l'historique du client. On refuse, et on oriente vers
    la suspension — qui coupe la collecte sans rien détruire."""
    d = _domain()
    tid = client.post("/admin/tenants", json={"domain": d}).json()["id"]
    cleanup.append(tid)

    with get_session() as db:
        em = Email(tenant_id=tid, message_id=f"m-{uuid.uuid4()}",
                   from_address="noreply-dmarc-support@google.com", subject="s",
                   received_at=datetime.now(timezone.utc), raw_object_key="raw/x.eml",
                   status="parsed_ok")
        db.add(em)
        db.flush()
        db.add(Report(tenant_id=tid, email_id=em.id, source_type="attachment",
                      status="ok", row_count=3, kind="dmarc"))
        db.commit()

    r = client.delete(f"/admin/tenants/{tid}")
    assert r.status_code == 409
    assert "Suspendez-le" in r.json()["detail"]

    with get_session() as db:                    # rien n'a été détruit
        assert db.get(Tenant, tid) is not None
        assert db.query(Report).filter_by(tenant_id=tid).count() == 1
