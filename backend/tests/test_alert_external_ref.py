"""La colonne qui relie une alerte au ticket ouvert pour elle.

Sans elle, on ne pourrait pas retrouver le ticket a annoter quand l'alerte se ferme.
"""
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from app.db.models import Alert, Tenant
from app.db.session import get_session, tenant_scoped_session


@pytest.fixture
def tenant():
    with get_session() as db:
        t = Tenant(domain=f"ext-{uuid.uuid4().hex[:8]}.test", name="Ext")
        db.add(t)
        db.commit()
        tid = str(t.id)
    yield tid
    with get_session() as db:
        db.query(Alert).filter_by(tenant_id=tid).delete()
        db.query(Tenant).filter_by(id=tid).delete()
        db.commit()


def test_external_ref_stocke_et_relu(tenant):
    with tenant_scoped_session(tenant_id=tenant) as db:
        a = Alert(tenant_id=tenant, kind="never_reported", dedup_key="", severity="critical",
                  payload={}, opened_at=datetime.now(timezone.utc), external_ref="TCK-4242")
        db.add(a)
        db.flush()
        assert db.get(Alert, a.id).external_ref == "TCK-4242"


def test_external_ref_par_defaut_nul(tenant):
    with tenant_scoped_session(tenant_id=tenant) as db:
        a = Alert(tenant_id=tenant, kind="domain_silent", dedup_key="", severity="critical",
                  payload={}, opened_at=datetime.now(timezone.utc))
        db.add(a)
        db.flush()
        assert db.get(Alert, a.id).external_ref is None


def test_colonne_presente_en_base():
    with get_session() as db:
        cols = db.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='alert' AND column_name='external_ref'")).all()
    assert cols, "colonne external_ref absente"
