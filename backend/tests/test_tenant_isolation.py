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


def test_worker_sees_all_tenants(seed_two_tenants):
    tid_a, tid_b = seed_two_tenants
    with get_session() as db:  # plan système, BYPASSRLS
        seen = {str(r.tenant_id) for r in db.query(Report).all()}
        assert {tid_a, tid_b} <= seen
