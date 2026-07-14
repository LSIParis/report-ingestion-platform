"""« Puis-je passer MTA-STS en enforce sans perdre de courrier ? »

Tests qui portent le poids :
 - `test_pas_de_double_comptage` : le résumé et le détail comptent les mêmes sessions.
 - `test_aucun_rapport_nest_pas_une_preuve_de_succes` : l'erreur qui coûterait cher.
   Un domaine silencieux doit s'entendre dire « on ne sait pas », jamais « c'est sûr ».
 - `test_un_compteur_absent_nest_pas_un_zero` : un `failed_sessions` absent/nul ne doit
   jamais être lu comme « aucun échec ».
 - `test_pas_de_double_comptage_entre_plusieurs_rapports` : le cas réel — plusieurs
   fournisseurs, le même jour, décrivant le même problème.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.admin import router as admin_router
from app.auth.middleware import TenantContext
from app.db.models import Email, Report, ReportRow, Tenant
from app.db.session import get_session, tenant_scoped_session
from app.services.tls_posture import posture


@pytest.fixture
def tenant_tls():
    """Un tenant avec un rapport vide. Chaque test y sème les lignes TLS dont il a besoin.
    Renvoie (tenant_id, report_id), tous deux en `str`."""
    with get_session() as db:
        t = Tenant(domain="tls-test.example", name="TLS")
        db.add(t)
        db.flush()
        em = Email(tenant_id=t.id, message_id=f"tls-{uuid.uuid4()}",
                   from_address="noreply@google.com", subject="s",
                   received_at=datetime.now(timezone.utc),
                   raw_object_key="raw/x.eml", status="parsed_ok")
        db.add(em)
        db.flush()
        rep = Report(tenant_id=t.id, email_id=em.id, source_type="attachment", status="ok")
        db.add(rep)
        db.flush()
        ids = (str(t.id), str(em.id), str(rep.id))
        db.commit()

    yield ids[0], ids[2]

    # Filtré par tenant_id (pas par id précis) : certains tests créent un rapport
    # supplémentaire pour simuler un second fournisseur (voir `_nouveau_rapport`), le
    # nettoyage doit donc emporter TOUT ce qui appartient à ce tenant, pas seulement
    # ce que la fixture a elle-même créé.
    with get_session() as db:
        db.query(ReportRow).filter_by(tenant_id=ids[0]).delete()
        db.query(Report).filter_by(tenant_id=ids[0]).delete()
        db.query(Email).filter_by(tenant_id=ids[0]).delete()
        db.query(Tenant).filter_by(id=ids[0]).delete()
        db.commit()


def _seme(tid: str, rid: str, data: dict, jours: int = 1) -> None:
    """Ajoute une ligne TLS datée d'il y a `jours` jours."""
    d = (datetime.now(timezone.utc) - timedelta(days=jours)).date().isoformat()
    with get_session() as db:
        db.add(ReportRow(tenant_id=tid, report_id=rid,
                         data={"reporter": "Google Inc.", "report_date": d,
                               "policy_domain": "tls-test.example", **data}))
        db.commit()


def test_aucun_rapport_nest_pas_une_preuve_de_succes(tenant_tls):
    """L'erreur qui coûterait du courrier : conclure « c'est sûr » d'un silence."""
    tid, _ = tenant_tls

    with tenant_scoped_session(tenant_id=tid) as db:
        p = posture(db, days=30)

    assert p["sessions_total"] == 0
    assert p["safe_to_enforce"] is False       # « on ne sait pas », surtout pas « oui »


def test_sessions_sans_echec_autorisent_enforce(tenant_tls):
    tid, rid = tenant_tls
    _seme(tid, rid, {"kind": "summary", "successful_sessions": 1000,
                     "failed_sessions": 0})

    with tenant_scoped_session(tenant_id=tid) as db:
        p = posture(db, days=30)

    assert p["sessions_ok"] == 1000
    assert p["sessions_failed"] == 0
    assert p["sessions_total"] == 1000
    assert p["failures"] == []
    assert p["incomplete_rows"] == 0            # un vrai zéro n'est pas une absence
    assert p["safe_to_enforce"] is True
    assert p["reporters"] == ["Google Inc."]


def test_pas_de_double_comptage(tenant_tls):
    """Le résumé dit 3 échecs, le détail détaille ces mêmes 3 échecs. Le total doit être
    3 — pas 6. C'est tout l'intérêt des noms de compteurs distincts."""
    tid, rid = tenant_tls
    _seme(tid, rid, {"kind": "summary", "successful_sessions": 997,
                     "failed_sessions": 3})
    _seme(tid, rid, {"kind": "failure",
                     "result_type": "certificate-host-mismatch",
                     "sending_mta_ip": "203.0.113.5",
                     "receiving_mx_hostname": "mx-backup.tls-test.example",
                     "failure_sessions": 3})

    with tenant_scoped_session(tenant_id=tid) as db:
        p = posture(db, days=30)

    assert p["sessions_failed"] == 3           # PAS 6
    assert p["sessions_total"] == 1000
    assert p["safe_to_enforce"] is False
    assert p["failures"] == [{
        "result_type": "certificate-host-mismatch",
        "sessions": 3,
        "sending_mta_ip": "203.0.113.5",
        "receiving_mx_hostname": "mx-backup.tls-test.example",
    }]


def test_hors_fenetre_est_ignore(tenant_tls):
    tid, rid = tenant_tls
    _seme(tid, rid, {"kind": "summary", "successful_sessions": 10,
                     "failed_sessions": 5}, jours=90)

    with tenant_scoped_session(tenant_id=tid) as db:
        p = posture(db, days=30)

    assert p["sessions_total"] == 0
    assert p["safe_to_enforce"] is False


def test_un_compteur_absent_nest_pas_un_zero(tenant_tls):
    """Le piège central : `failed_sessions` (ou `successful_sessions`) absent ou nul
    n'est PAS un zéro. Un fournisseur peut envoyer un `summary` JSON valide avec
    `"total-failure-session-count": null` — la clé existe, l'adaptateur ne lève rien,
    le normaliseur écrit `None`. Si on le comptait comme 0, un rapport qui dit
    littéralement « je ne sais pas » ferait basculer `safe_to_enforce` à `true`."""
    tid, rid = tenant_tls
    # failed_sessions manquant : on ne sait rien des échecs de cette ligne.
    _seme(tid, rid, {"kind": "summary", "successful_sessions": 1000,
                     "failed_sessions": None})
    # successful_sessions manquant cette fois : incomplet dans l'autre sens.
    _seme(tid, rid, {"kind": "summary", "successful_sessions": None,
                     "failed_sessions": 0})
    # Une ligne parfaitement lisible, avec un VRAI zéro : ne doit pas être comptée.
    _seme(tid, rid, {"kind": "summary", "successful_sessions": 500,
                     "failed_sessions": 0})

    with tenant_scoped_session(tenant_id=tid) as db:
        p = posture(db, days=30)

    assert p["incomplete_rows"] == 2
    # Deux lignes sur trois ne disent rien de fiable : jamais « c'est sûr ».
    assert p["safe_to_enforce"] is False


def _nouveau_rapport(tid: str, reporter: str) -> str:
    """Un e-mail + rapport supplémentaire pour le même tenant, comme si un second
    fournisseur (Microsoft, en plus de Google) avait envoyé son propre rapport le
    même jour — le cas qui casserait une agrégation naïve par report_id."""
    with get_session() as db:
        em = Email(tenant_id=tid, message_id=f"tls-{reporter}-{uuid.uuid4()}",
                   from_address=f"noreply@{reporter}.example", subject="s",
                   received_at=datetime.now(timezone.utc),
                   raw_object_key="raw/x.eml", status="parsed_ok")
        db.add(em)
        db.flush()
        rep = Report(tenant_id=tid, email_id=em.id, source_type="attachment", status="ok")
        db.add(rep)
        db.flush()
        rid = str(rep.id)
        db.commit()
    return rid


def _seme_pour(tid: str, rid: str, reporter: str, data: dict) -> None:
    """Comme `_seme`, mais avec un `reporter` explicite (pas toujours Google)."""
    d = datetime.now(timezone.utc).date().isoformat()
    with get_session() as db:
        db.add(ReportRow(tenant_id=tid, report_id=rid,
                         data={"reporter": reporter, "report_date": d,
                               "policy_domain": "tls-test.example", **data}))
        db.commit()


def test_pas_de_double_comptage_entre_plusieurs_rapports(tenant_tls):
    """Le cas réel : plusieurs politiques, plusieurs fournisseurs, le même jour,
    décrivant le MÊME problème. Deux garanties à prouver ensemble :

     1. le total d'échecs vient UNIQUEMENT des lignes `summary` (jamais des lignes
        `failure`, quel que soit le nombre de rapports) ;
     2. des échecs identiques venant de rapports DIFFÉRENTS s'additionnent en une
        seule entrée dans `failures` (même triplet = même cause), au lieu de produire
        deux entrées ou de s'écraser l'une l'autre.
    """
    tid, rid_google = tenant_tls
    rid_microsoft = _nouveau_rapport(tid, "Microsoft Corp.")

    # Google : 998 ok, 2 échecs — détaillés par une ligne failure.
    _seme_pour(tid, rid_google, "Google Inc.",
               {"kind": "summary", "successful_sessions": 998, "failed_sessions": 2})
    _seme_pour(tid, rid_google, "Google Inc.",
               {"kind": "failure", "result_type": "certificate-expired",
                "sending_mta_ip": "203.0.113.9",
                "receiving_mx_hostname": "mx.tls-test.example",
                "failure_sessions": 2})

    # Microsoft : 499 ok, 5 échecs, MÊME triplet (même certificat expiré, même MX).
    _seme_pour(tid, rid_microsoft, "Microsoft Corp.",
               {"kind": "summary", "successful_sessions": 499, "failed_sessions": 5})
    _seme_pour(tid, rid_microsoft, "Microsoft Corp.",
               {"kind": "failure", "result_type": "certificate-expired",
                "sending_mta_ip": "203.0.113.9",
                "receiving_mx_hostname": "mx.tls-test.example",
                "failure_sessions": 5})

    with tenant_scoped_session(tenant_id=tid) as db:
        p = posture(db, days=30)

    # Le total vient des seules lignes summary : 998 + 499 ok, 2 + 5 échecs.
    # PAS 998 + 499 + 2 + 5 (détail) ni 2*(2+5) (double lecture des failure).
    assert p["sessions_ok"] == 1497
    assert p["sessions_failed"] == 7
    assert p["sessions_total"] == 1504
    # Une seule entrée : les deux rapports décrivent la même cause, les sessions
    # s'additionnent (2 + 5 = 7), pas deux entrées et pas un écrasement à 2 ou 5.
    assert p["failures"] == [{
        "result_type": "certificate-expired",
        "sessions": 7,
        "sending_mta_ip": "203.0.113.9",
        "receiving_mx_hostname": "mx.tls-test.example",
    }]
    assert p["incomplete_rows"] == 0
    assert p["safe_to_enforce"] is False
    assert p["reporters"] == ["Google Inc.", "Microsoft Corp."]


# --------------------------------------------------- cohérence de la route
@pytest.fixture
def admin_client():
    app = FastAPI()
    ctx = TenantContext(user="admin@lsi.test", role="platform_admin", tenant_ids=(),
                        active_tenant=None, bypass=True)

    @app.middleware("http")
    async def inject_ctx(request, call_next):
        request.state.tenant = ctx
        return await call_next(request)

    app.include_router(admin_router)
    return TestClient(app)


def test_tenant_inconnu_renvoie_404(admin_client):
    """Aligné sur `get_mta_sts` : un domaine inexistant doit dire 404, pas
    `sessions_total: 0` — sinon un tenant supprimé et un tenant silencieux
    deviennent indiscernables dans la réponse."""
    r = admin_client.get(f"/admin/tenants/{uuid.uuid4()}/tls-posture")
    assert r.status_code == 404


def test_tenant_connu_renvoie_la_posture(admin_client, tenant_tls):
    tid, rid = tenant_tls
    _seme(tid, rid, {"kind": "summary", "successful_sessions": 10,
                     "failed_sessions": 0})

    r = admin_client.get(f"/admin/tenants/{tid}/tls-posture")
    assert r.status_code == 200
    assert r.json()["sessions_total"] == 10
