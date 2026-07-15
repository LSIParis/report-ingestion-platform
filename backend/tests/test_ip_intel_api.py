"""La route d'enrichissement. Le DNS est moqué : ces tests ne touchent pas le réseau.

Le contrôle d'appartenance est testé ici ET dans test_tenant_isolation.py (bloquant).
Il est le seul rempart entre le cache — qui n'a pas de tenant_id — et une fuite
d'existence entre clients.
"""
import uuid
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth.middleware import TenantContext
from app.db.models import Email, IpIntel, Report, ReportRow, Tenant
from app.db.session import get_session
from app.services.ip_intel import IpFacts
from app.services.spf import SpfVerdict


@pytest.fixture
def tenant_avec_ligne_dmarc():
    """Un tenant, un rapport, une ligne DMARC portant l'IP 203.0.113.9."""
    with get_session() as db:
        t = Tenant(domain="ip-test.example", name="IP Test")
        db.add(t)
        db.flush()
        em = Email(tenant_id=t.id, message_id=f"ip-{uuid.uuid4()}",
                   from_address="reports@ip-test.example", subject="t",
                   received_at=datetime.now(timezone.utc),
                   raw_object_key="raw/t.eml", status="parsed_ok")
        db.add(em)
        db.flush()
        rep = Report(tenant_id=t.id, email_id=em.id, source_type="attachment", status="ok", kind="dmarc")
        db.add(rep)
        db.flush()
        db.add(ReportRow(tenant_id=t.id, report_id=rep.id, data={
            "source_ip": "203.0.113.9", "message_count": 412, "disposition": "none",
            "spf": "fail", "dkim": "fail", "aligned": "fail",
            "header_from": "ip-test.example", "auth_spf": "usurpateur.example",
            "auth_dkim": None, "report_date": "2026-07-13",
        }))
        db.commit()
        ids = (str(t.id), str(em.id), str(rep.id))

    yield ids

    with get_session() as db:
        db.query(ReportRow).filter_by(report_id=ids[2]).delete()
        db.query(Report).filter_by(id=ids[2]).delete()
        db.query(Email).filter_by(id=ids[1]).delete()
        db.query(Tenant).filter_by(id=ids[0]).delete()
        db.query(IpIntel).filter_by(ip="203.0.113.9").delete()
        db.commit()


@pytest.fixture
def client_du_tenant(tenant_avec_ligne_dmarc, monkeypatch):
    """TestClient scopé sur ce tenant, DNS moqué.

    Même montage que `tests/test_admin_domains.py` : une app neuve avec le seul routeur
    et un TenantContext injecté. `bypass=False` — le client est un vrai tenant, soumis à
    la RLS : c'est précisément ce qu'on veut éprouver ici.
    """
    from app.api import ip_intel as route

    tid = tenant_avec_ligne_dmarc[0]

    monkeypatch.setattr(route.ip_intel, "lookup", lambda ip: IpFacts(
        ip=ip, ptr="o1.ptr1234.sendgrid.net", fcrdns=True,
        asn=11377, as_org="SENDGRID, US", country="US"))
    monkeypatch.setattr(route.spf, "covers",
                        lambda domain, ip: SpfVerdict(result="fail", mechanism="-all"))

    app = FastAPI()
    ctx = TenantContext(user="viewer@ip-test.example", role="tenant_viewer",
                        tenant_ids=(tid,), active_tenant=tid, bypass=False)

    @app.middleware("http")
    async def inject_ctx(request, call_next):
        request.state.tenant = ctx
        return await call_next(request)

    app.include_router(route.router)
    yield TestClient(app), tid


def test_ip_inconnue_du_tenant_donne_404(client_du_tenant):
    client, _ = client_du_tenant

    r = client.get("/ip-intel/198.51.100.1")

    assert r.status_code == 404


def test_ip_connue_renvoie_faits_verdict_et_activite(client_du_tenant):
    client, _ = client_du_tenant

    r = client.get("/ip-intel/203.0.113.9")

    assert r.status_code == 200
    b = r.json()
    assert b["ptr"] == "o1.ptr1234.sendgrid.net"
    assert b["fcrdns"] is True
    assert b["sender"]["name"] == "SendGrid"          # PTR vérifié → identifié
    assert b["hosted_by"] is None
    assert b["spf"]["result"] == "fail"               # reconnu MAIS non autorisé
    assert b["activity"]["messages"] == 412
    assert b["activity"]["dispositions"] == {"none": 412}
    assert b["activity"]["spf_domains"] == ["usurpateur.example"]


def test_le_cache_evite_une_seconde_resolution_dns(client_du_tenant, monkeypatch):
    from app.api import ip_intel as route

    client, _ = client_du_tenant
    appels = {"n": 0}

    def compte(ip):
        appels["n"] += 1
        return IpFacts(ip=ip, ptr="x.sendgrid.net", fcrdns=True)

    monkeypatch.setattr(route.ip_intel, "lookup", compte)

    client.get("/ip-intel/203.0.113.9")
    client.get("/ip-intel/203.0.113.9")

    assert appels["n"] == 1, "le second appel aurait dû être servi par le cache"


def test_refresh_force_une_nouvelle_resolution(client_du_tenant, monkeypatch):
    from app.api import ip_intel as route

    client, _ = client_du_tenant
    appels = {"n": 0}

    def compte(ip):
        appels["n"] += 1
        return IpFacts(ip=ip, ptr="x.sendgrid.net", fcrdns=True)

    monkeypatch.setattr(route.ip_intel, "lookup", compte)

    client.get("/ip-intel/203.0.113.9")
    client.post("/ip-intel/203.0.113.9/refresh")

    assert appels["n"] == 2


def test_refresh_sur_ip_inconnue_donne_404(client_du_tenant):
    client, _ = client_du_tenant
    assert client.post("/ip-intel/198.51.100.1/refresh").status_code == 404


def test_ip_syntaxiquement_invalide_donne_400(client_du_tenant):
    client, _ = client_du_tenant
    assert client.get("/ip-intel/pas-une-ip").status_code == 400


@pytest.fixture
def ligne_tls(tenant_avec_ligne_dmarc):
    """Une ligne d'échec TLS portant une IP qu'aucune ligne DMARC ne connaît."""
    tid, _, rep_id = tenant_avec_ligne_dmarc
    with get_session() as db:
        db.add(ReportRow(tenant_id=tid, report_id=rep_id, data={
            "kind": "failure", "result_type": "certificate-host-mismatch",
            "sending_mta_ip": "203.0.113.44",
            "receiving_mx_hostname": "mx-backup.ip-test.example",
            "failure_sessions": 7, "policy_domain": "ip-test.example",
            "reporter": "Google Inc.", "report_date": "2026-07-13",
        }))
        db.commit()
    yield "203.0.113.44"
    with get_session() as db:
        db.query(ReportRow).filter(
            ReportRow.data["sending_mta_ip"].astext == "203.0.113.44").delete(
            synchronize_session=False)
        db.query(IpIntel).filter_by(ip="203.0.113.44").delete()
        db.commit()


def test_ip_vue_uniquement_en_TLS_est_consultable(client_du_tenant, ligne_tls):
    """Sans l'extension du contrôle d'appartenance, cette IP donnerait 404 — alors que le
    tenant la voit dans ses propres rapports."""
    client, _ = client_du_tenant

    r = client.get(f"/ip-intel/{ligne_tls}")

    assert r.status_code == 200


def test_activite_TLS_est_comptee(client_du_tenant, ligne_tls):
    """Sinon le panneau afficherait « 0 message » sur une IP qui a bel et bien échoué."""
    client, _ = client_du_tenant

    a = client.get(f"/ip-intel/{ligne_tls}").json()["activity"]

    assert a["messages"] == 0                      # aucune ligne DMARC : c'est vrai
    assert a["tls_sessions"] == 7
    assert a["tls_partial"] is False
    assert a["tls_failures"] == {
        "certificate-host-mismatch": {"sessions": 7, "partial": False}}
    # La ligne TLS porte un report_date valide : la période ne doit pas rester vide.
    assert a["first_seen"] == "2026-07-13"
    assert a["last_seen"] == "2026-07-13"


def test_activite_TLS_sans_ligne_failure_est_un_vrai_zero(client_du_tenant):
    """Aucune ligne `failure` TLS pour cette IP (elle n'a été vue qu'en DMARC) :
    `tls_sessions` doit être `0` (un vrai zéro, aucun échec TLS observé) et non
    `None` -- les deux ne veulent pas dire la même chose."""
    client, _ = client_du_tenant

    a = client.get("/ip-intel/203.0.113.9").json()["activity"]

    assert a["tls_sessions"] == 0
    assert a["tls_partial"] is False
    assert a["tls_failures"] == {}


def test_activite_TLS_echec_sans_nombre_nest_pas_affiche_comme_zero(
        client_du_tenant, ligne_tls):
    """Le bug corrigé : un `failure_sessions: null` produisait une entrée
    `{"certificate-expired": 0}` -- un échec avéré affiché à zéro (`or 0` traitait
    `null` comme un vrai zéro) -- et sous-comptait `tls_sessions` en silence. Une
    session TLS en échec dont le nombre est illisible doit rester `None` (inconnu),
    jamais `0` (aucun échec) : c'est exactement le contrat de `tls_posture`.

    Deux lignes sur la MÊME IP : `ligne_tls` (7, lisible) et celle semée ici (`null`,
    illisible) -- le connu (7) ne doit pas être effacé par l'inconnu, mais `partial`
    doit le signaler.
    """
    client, tid = client_du_tenant
    with get_session() as db:
        rep_id = db.query(Report.id).filter_by(tenant_id=tid).first()[0]
        db.add(ReportRow(tenant_id=tid, report_id=rep_id, data={
            "kind": "failure", "result_type": "certificate-expired",
            "sending_mta_ip": "203.0.113.44", "failure_sessions": None,
            "policy_domain": "ip-test.example", "reporter": "Microsoft Corp.",
            "report_date": "2026-07-13",
        }))
        db.commit()

    a = client.get(f"/ip-intel/{ligne_tls}").json()["activity"]

    assert a["tls_sessions"] == 7
    assert a["tls_partial"] is True
    assert a["tls_failures"] == {
        "certificate-host-mismatch": {"sessions": 7, "partial": False},
        "certificate-expired": {"sessions": None, "partial": True},
    }
