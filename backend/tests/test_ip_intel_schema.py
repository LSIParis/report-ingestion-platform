"""Le cache ip_intel : accessible aux deux plans, SANS tenant_id — et c'est assumé.

Ce sont des faits publics sur Internet, pas des données de client (comme `tenant` ou
`audit_log`, déjà hors RLS dans 0002). Ce qui ferme la fuite entre clients n'est pas la
RLS sur cette table : c'est le contrôle d'appartenance de la route, qui exige que l'IP
soit déjà visible du tenant AVANT toute lecture du cache.
"""
from datetime import datetime, timezone

from sqlalchemy import text

from app.db.models import IpIntel
from app.db.session import get_session, tenant_scoped_session


def test_le_plan_api_peut_lire_et_ecrire_le_cache():
    with tenant_scoped_session(tenant_id=None) as db:
        db.add(IpIntel(ip="203.0.113.7", ptr="test.exemple.invalid", fcrdns=False,
                       asn=64500, as_org="TEST-AS", country="FR",
                       checked_at=datetime.now(timezone.utc)))
        db.flush()
        assert db.get(IpIntel, "203.0.113.7") is not None
        db.rollback()


def test_index_de_recherche_par_ip_source_existe():
    """Sans lui, chaque ouverture du panneau ferait un seq scan sur report_row."""
    with get_session() as db:
        rows = db.execute(text(
            "SELECT indexname FROM pg_indexes "
            "WHERE tablename = 'report_row' AND indexname = 'ix_report_row_source_ip'"
        )).all()
    assert rows, "index ix_report_row_source_ip absent"


def test_ip_intel_na_pas_de_rls_et_c_est_voulu():
    with get_session() as db:
        enabled = db.execute(text(
            "SELECT relrowsecurity FROM pg_class WHERE relname = 'ip_intel'"
        )).scalar()
    assert enabled is False
