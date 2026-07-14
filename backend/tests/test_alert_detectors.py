"""Les trois détecteurs.

Ils ont des formes délibérément différentes — l'un lit des lignes, l'autre lit une
ABSENCE, le troisième lit une date de création. Un cadre validé par un seul cas n'est pas
un cadre.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.db.models import Email, Report, ReportRow, Tenant
from app.db.session import get_session, tenant_scoped_session
from app.services.alerting.base import all_conditions


@pytest.fixture
def domaine():
    """Un tenant actif, créé il y a 30 jours. Chaque test lui ajoute ce dont il a besoin."""
    with get_session() as db:
        t = Tenant(domain=f"det-{uuid.uuid4().hex[:8]}.test", name="Detect",
                   created_at=datetime.now(timezone.utc) - timedelta(days=30))
        db.add(t)
        db.commit()
        tid = str(t.id)

    yield tid

    with get_session() as db:
        reps = [r.id for r in db.query(Report).filter_by(tenant_id=tid).all()]
        if reps:
            db.query(ReportRow).filter(ReportRow.report_id.in_(reps)).delete(
                synchronize_session=False)
            db.query(Report).filter(Report.id.in_(reps)).delete(synchronize_session=False)
        db.query(Email).filter_by(tenant_id=tid).delete()
        db.query(Tenant).filter_by(id=tid).delete()
        db.commit()


def _rapport(tid: str, *, il_y_a_jours: int, profil: str = "_default_dmarc_xml"):
    """Un rapport reçu il y a N jours. Renvoie son id."""
    quand = datetime.now(timezone.utc) - timedelta(days=il_y_a_jours)
    with get_session() as db:
        em = Email(tenant_id=tid, message_id=f"det-{uuid.uuid4()}",
                   from_address="noreply@google.com", subject="s",
                   received_at=quand, raw_object_key="raw/x.eml", status="parsed_ok")
        db.add(em)
        db.flush()
        rep = Report(tenant_id=tid, email_id=em.id, source_type="attachment",
                     profile_id=profil, status="ok", created_at=quand)
        db.add(rep)
        db.flush()
        rid = str(rep.id)
        db.commit()
    return rid


def _ligne_tls_en_echec(tid: str, rid: str):
    hier = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
    with get_session() as db:
        db.add(ReportRow(tenant_id=tid, report_id=rid, data={
            "kind": "summary", "successful_sessions": 100, "failed_sessions": 3,
            "policy_domain": "det.test", "reporter": "Google Inc.", "report_date": hier}))
        db.add(ReportRow(tenant_id=tid, report_id=rid, data={
            "kind": "failure", "result_type": "certificate-host-mismatch",
            "sending_mta_ip": "203.0.113.5", "receiving_mx_hostname": "mx.det.test",
            "failure_sessions": 3, "policy_domain": "det.test",
            "reporter": "Google Inc.", "report_date": hier}))
        db.commit()


def _conditions(tid: str):
    with tenant_scoped_session(tenant_id=tid) as db:
        tenant = db.get(Tenant, tid)
        return all_conditions(db, tenant)


def _mode(tid: str, mode: str):
    with get_session() as db:
        db.get(Tenant, tid).mta_sts_mode = mode
        db.commit()


# ---------------------------------------------------------------- never_reported
def test_never_reported_apres_le_delai_de_grace(domaine):
    """LA plus précieuse : le client qu'on croit protégé et qui ne l'est pas. Rien, dans
    l'application, ne le distingue aujourd'hui d'un client tranquille."""
    kinds = [c.kind for c in _conditions(domaine)]

    assert "never_reported" in kinds
    c = next(c for c in _conditions(domaine) if c.kind == "never_reported")
    assert c.severity == "critical"
    assert c.dedup_key == ""


def test_never_reported_se_tait_pendant_le_delai_de_grace():
    """Un domaine ajouté hier n'a pas encore eu le temps de publier son DMARC."""
    with get_session() as db:
        t = Tenant(domain=f"neuf-{uuid.uuid4().hex[:8]}.test", name="Neuf",
                   created_at=datetime.now(timezone.utc) - timedelta(days=1))
        db.add(t)
        db.commit()
        tid = str(t.id)
    try:
        assert [c.kind for c in _conditions(tid)] == []
    finally:
        with get_session() as db:
            db.query(Tenant).filter_by(id=tid).delete()
            db.commit()


def test_never_reported_disparait_des_le_premier_rapport(domaine):
    _rapport(domaine, il_y_a_jours=0)
    assert "never_reported" not in [c.kind for c in _conditions(domaine)]


# ---------------------------------------------------------------- domain_silent
def test_domain_silent_quand_les_rapports_ont_cesse(domaine):
    """Le signal MUET : aucun écran ne le montrera jamais, puisqu'il ne s'y passe rien."""
    _rapport(domaine, il_y_a_jours=10)

    conds = _conditions(domaine)
    silent = next((c for c in conds if c.kind == "domain_silent"), None)

    assert silent is not None
    assert silent.severity == "critical"
    assert silent.payload["silence_days"] >= 10


def test_domain_silent_se_tait_si_un_rapport_est_recent(domaine):
    _rapport(domaine, il_y_a_jours=1)
    assert "domain_silent" not in [c.kind for c in _conditions(domaine)]


def test_un_domaine_qui_n_a_jamais_rapporte_n_est_pas_dit_silencieux(domaine):
    """Il n'est pas devenu silencieux : il n'a jamais parlé. C'est never_reported qui s'en
    occupe. Confondre les deux enverrait l'exploitant chercher une panne au lieu d'une
    procédure jamais terminée."""
    kinds = [c.kind for c in _conditions(domaine)]
    assert "never_reported" in kinds
    assert "domain_silent" not in kinds


# ---------------------------------------------------------------- tls_failure
def test_tls_failure_est_un_avertissement_en_mode_testing(domaine):
    rid = _rapport(domaine, il_y_a_jours=1, profil="_default_tlsrpt_json")
    _ligne_tls_en_echec(domaine, rid)
    _mode(domaine, "testing")

    c = next(c for c in _conditions(domaine) if c.kind == "tls_failure")

    assert c.severity == "warning"
    assert "certificate-host-mismatch" in c.dedup_key
    assert "203.0.113.5" in c.dedup_key


def test_tls_failure_devient_CRITIQUE_en_mode_enforce(domaine):
    """MÊMES données, urgence radicalement différente : en enforce, du courrier est en
    train d'être REFUSÉ, maintenant."""
    rid = _rapport(domaine, il_y_a_jours=1, profil="_default_tlsrpt_json")
    _ligne_tls_en_echec(domaine, rid)
    _mode(domaine, "enforce")

    c = next(c for c in _conditions(domaine) if c.kind == "tls_failure")

    assert c.severity == "critical"


def test_un_detecteur_casse_ne_prive_pas_des_autres(domaine, monkeypatch):
    """Un détecteur qui lève ne doit pas faire disparaître les alertes des deux autres."""
    from app.services.alerting import base

    def casse(db, tenant):
        raise RuntimeError("boum")

    monkeypatch.setitem(base._DETECTORS, "tls_failure", casse)

    kinds = [c.kind for c in _conditions(domaine)]

    assert "never_reported" in kinds      # les autres ont bien tourné
