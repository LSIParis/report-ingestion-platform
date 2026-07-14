"""« Puis-je passer MTA-STS en enforce sans perdre de courrier ? »

Deux tests portent le poids :
 - `test_pas_de_double_comptage` : le résumé et le détail comptent les mêmes sessions.
 - `test_aucun_rapport_nest_pas_une_preuve_de_succes` : l'erreur qui coûterait cher.
   Un domaine silencieux doit s'entendre dire « on ne sait pas », jamais « c'est sûr ».
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

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

    with get_session() as db:
        db.query(ReportRow).filter_by(report_id=ids[2]).delete()
        db.query(Report).filter_by(id=ids[2]).delete()
        db.query(Email).filter_by(id=ids[1]).delete()
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
