"""Règles d'attribution : validation et banc d'essai.

La résolution décide À QUI appartient un rapport. Une règle mal formée n'échoue pas —
elle range silencieusement les données d'un client chez un autre. Ces gardes sont donc
des gardes d'isolation, pas de la validation de formulaire.

Le cas fatal : une règle `sender`. La cascade l'évalue EN PREMIER et la juge certaine.
Or tous les rapports DMARC viennent des mêmes expéditeurs (google, microsoft…) : une
telle règle raflerait les rapports de TOUS les domaines pour un seul.
"""
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.admin import router
from app.auth.middleware import TenantContext
from app.db.models import Tenant, TenantMatchingRule
from app.db.session import get_session
from app.services.tenants import dmarc_subject_pattern


@pytest.fixture
def client():
    app = FastAPI()
    ctx = TenantContext(user="admin-rules@lsi.test", role="platform_admin",
                        tenant_ids=(), active_tenant=None, bypass=True)

    @app.middleware("http")
    async def inject_ctx(request, call_next):
        request.state.tenant = ctx
        return await call_next(request)

    app.include_router(router)
    return TestClient(app)


@pytest.fixture
def domains(client):
    """Deux domaines réels, chacun avec sa règle DMARC."""
    made = []
    for _ in range(2):
        d = f"r-{uuid.uuid4().hex[:8]}.test"
        made.append((client.post("/admin/tenants", json={"domain": d}).json()["id"], d))

    yield made

    with get_session() as db:
        for tid, _ in made:
            db.query(TenantMatchingRule).filter_by(tenant_id=tid).delete()
            db.query(Tenant).filter_by(id=tid).delete()
        db.commit()


# --------------------------------------------------- le garde-fou qui compte
@pytest.mark.parametrize("pattern", [
    "google.com",                      # capte tous les rapports Google
    "microsoft.com",
    "dmarcreport@microsoft.com",
    "noreply-dmarc-support@google.com",
    "com",                             # capte à peu près tout
])
def test_regle_sender_captant_un_expediteur_de_rapports_est_refusee(client, domains, pattern):
    tid, _ = domains[0]
    r = client.post("/admin/rules", json={
        "tenant_id": tid, "rule_type": "sender", "pattern": pattern, "priority": 10})
    assert r.status_code == 422
    assert "TOUS les domaines" in r.json()["detail"]


def test_regle_sender_legitime_reste_possible(client, domains):
    """On n'interdit pas le type `sender` : seulement les motifs qui captent les
    expéditeurs de rapports. Un flux de marque classique reste configurable."""
    tid, _ = domains[0]
    r = client.post("/admin/rules", json={
        "tenant_id": tid, "rule_type": "sender",
        "pattern": "reports@marque-cliente.fr", "priority": 10})
    assert r.status_code == 201


# --------------------------------------------------- autres validations
def test_regex_invalide_refusee(client, domains):
    tid, _ = domains[0]
    r = client.post("/admin/rules", json={
        "tenant_id": tid, "rule_type": "subject_regex", "pattern": "domain:[a-z", "priority": 20})
    assert r.status_code == 422
    assert "régulière invalide" in r.json()["detail"]


def test_motif_trop_court_refuse(client, domains):
    tid, _ = domains[0]
    r = client.post("/admin/rules", json={
        "tenant_id": tid, "rule_type": "keyword", "pattern": "a", "priority": 30})
    assert r.status_code == 422


def test_type_inconnu_refuse(client, domains):
    tid, _ = domains[0]
    r = client.post("/admin/rules", json={
        "tenant_id": tid, "rule_type": "magie", "pattern": "xxx", "priority": 30})
    assert r.status_code == 422


# --------------------------------------------------- ordre d'évaluation
def test_les_regles_sont_listees_dans_l_ordre_de_la_cascade(client, domains):
    tid, _ = domains[0]
    client.post("/admin/rules", json={"tenant_id": tid, "rule_type": "alias",
                                      "pattern": "un alias", "priority": 5})
    client.post("/admin/rules", json={"tenant_id": tid, "rule_type": "keyword",
                                      "pattern": "un mot", "priority": 5})

    types = [r["rule_type"] for r in client.get("/admin/rules").json()]
    # subject_regex (créé avec les domaines) passe AVANT keyword, qui passe avant alias,
    # quelle que soit la priorité — c'est l'ordre réel de la cascade.
    assert types.index("subject_regex") < types.index("keyword") < types.index("alias")


# --------------------------------------------------- banc d'essai
def test_banc_dessai_attribue_au_bon_domaine(client, domains):
    _, domain = domains[0]
    r = client.post("/admin/rules/test", json={
        "from_address": "noreply-dmarc-support@google.com",
        "subject": f"Report domain: {domain} Submitter: google.com Report-ID: 42"}).json()

    assert r["domain"] == domain
    assert r["method"] == "subject_regex"
    assert r["quarantined"] is False


def test_banc_dessai_annonce_la_quarantaine(client, domains):
    r = client.post("/admin/rules/test", json={
        "from_address": "noreply-dmarc-support@google.com",
        "subject": "Report domain: inconnu-total.example Submitter: google.com"}).json()

    assert r["quarantined"] is True
    assert r["domain"] is None


def test_banc_dessai_rejette_le_suffixe_trompeur(client, domains):
    """'notXXX.test' ne doit PAS être attribué au domaine 'XXX.test'."""
    _, domain = domains[0]
    r = client.post("/admin/rules/test", json={
        "from_address": "noreply-dmarc-support@google.com",
        "subject": f"Report domain: not{domain} Submitter: google.com"}).json()

    assert r["quarantined"] is True


def test_desactiver_une_regle_la_retire_de_la_cascade(client, domains):
    tid, domain = domains[0]
    rule = next(r for r in client.get("/admin/rules").json()
                if r["tenant_id"] == tid and r["rule_type"] == "subject_regex")
    assert rule["pattern"] == dmarc_subject_pattern(domain)

    client.patch(f"/admin/rules/{rule['id']}", json={"is_active": False})

    r = client.post("/admin/rules/test", json={
        "from_address": "noreply-dmarc-support@google.com",
        "subject": f"Report domain: {domain} Submitter: google.com"}).json()
    assert r["quarantined"] is True     # plus aucune règle ne le reconnaît
