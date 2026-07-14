"""Test d'isolation cross-tenant. DOIT passer — bloquant en CI.
Valide que la RLS (plan app_api) empêche tout accès inter-tenant, en lecture ET écriture.
"""
import pytest

from app.db.models import Email, Report
from app.db.session import get_session, tenant_scoped_session


def test_tenant_a_cannot_read_tenant_b(seed_two_tenants):
    tid_a, tid_b = seed_two_tenants
    with tenant_scoped_session(tenant_id=tid_a) as db:
        rows = db.query(Report).all()
        assert all(str(r.tenant_id) == tid_a for r in rows)
        assert not any(str(r.tenant_id) == tid_b for r in rows)


def test_forged_insert_for_other_tenant_is_rejected(seed_two_tenants):
    tid_a, tid_b = seed_two_tenants
    with tenant_scoped_session(tenant_id=tid_a) as db:
        # email_id valide côté A pour satisfaire la FK ; tenant_id estampillé B (interdit).
        email_a = db.query(Email).first()
        db.add(Report(tenant_id=tid_b, email_id=email_a.id, source_type="body", status="ok"))
        with pytest.raises(Exception):
            db.flush()  # WITH CHECK doit rejeter l'écriture cross-tenant
        # Après l'erreur attendue, la transaction est en échec : on la nettoie pour
        # que le context manager puisse se refermer proprement.
        db.rollback()


def test_worker_sees_all_tenants(seed_two_tenants):
    tid_a, tid_b = seed_two_tenants
    with get_session() as db:  # plan système, BYPASSRLS
        seen = {str(r.tenant_id) for r in db.query(Report).all()}
        assert {tid_a, tid_b} <= seen


def test_ip_vue_par_b_est_invisible_de_a(seed_two_tenants):
    """Le cache ip_intel n'a pas de tenant_id. Ce qui empêche A de sonder l'existence
    d'une IP chez B, c'est le contrôle d'appartenance de la route : la requête ne trouve
    la ligne que si elle est visible SOUS RLS.

    Ce test valide la brique sur laquelle ce contrôle repose. S'il tombe, la route peut
    devenir un oracle : « cette IP est-elle dans votre cache ? » révélerait le trafic
    d'un autre client.
    """
    from app.db.models import ReportRow

    tid_a, tid_b = seed_two_tenants

    with get_session() as db:                       # plan worker : on sème chez B
        rep_b = db.query(Report).filter_by(tenant_id=tid_b).first()
        db.add(ReportRow(tenant_id=tid_b, report_id=rep_b.id,
                         data={"source_ip": "198.51.100.42", "message_count": 5}))
        db.commit()

    try:
        with tenant_scoped_session(tenant_id=tid_a) as db:   # A cherche l'IP de B
            vues = (db.query(ReportRow)
                      .filter(ReportRow.data["source_ip"].astext == "198.51.100.42")
                      .all())
            assert vues == [], "A voit une ligne de B : la route deviendrait un oracle"
    finally:
        with get_session() as db:
            db.query(ReportRow).filter(
                ReportRow.data["source_ip"].astext == "198.51.100.42").delete(
                synchronize_session=False)
            db.commit()


def test_ip_TLS_vue_par_b_est_invisible_de_a(seed_two_tenants):
    """Même principe que pour une IP DMARC : le contrôle d'appartenance de /ip-intel
    interroge maintenant DEUX champs. Il doit rester aveugle aux lignes des autres.
    """
    from app.db.models import ReportRow

    tid_a, tid_b = seed_two_tenants

    with get_session() as db:
        rep_b = db.query(Report).filter_by(tenant_id=tid_b).first()
        db.add(ReportRow(tenant_id=tid_b, report_id=rep_b.id,
                         data={"kind": "failure", "sending_mta_ip": "198.51.100.77",
                               "result_type": "starttls-not-supported",
                               "failure_sessions": 9}))
        db.commit()

    try:
        with tenant_scoped_session(tenant_id=tid_a) as db:
            vues = (db.query(ReportRow)
                      .filter(ReportRow.data["sending_mta_ip"].astext == "198.51.100.77")
                      .all())
            assert vues == [], "A voit une ligne TLS de B"
    finally:
        with get_session() as db:
            db.query(ReportRow).filter(
                ReportRow.data["sending_mta_ip"].astext == "198.51.100.77").delete(
                synchronize_session=False)
            db.commit()
