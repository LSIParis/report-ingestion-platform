"""La table des alertes. Deux garanties viennent de la BASE, pas du code.

 - La RLS : une alerte porte un tenant_id, donc elle est isolée comme toute table métier.
 - L'unicité d'une alerte OUVERTE par condition : un index unique partiel. Un bug du
   réconciliateur ne peut donc pas produire un doublon — il produit une erreur. La
   déduplication n'est pas une convention qu'on espère respectée, c'est une contrainte.
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
        t = Tenant(domain=f"alerte-{uuid.uuid4().hex[:8]}.test", name="Alerte")
        db.add(t)
        db.commit()
        tid = str(t.id)

    yield tid

    with get_session() as db:
        db.query(Alert).filter_by(tenant_id=tid).delete()
        db.query(Tenant).filter_by(id=tid).delete()
        db.commit()


def _alerte(tid, **kw):
    base = dict(tenant_id=tid, kind="tls_failure", dedup_key="k", severity="warning",
                payload={}, opened_at=datetime.now(timezone.utc))
    return Alert(**{**base, **kw})


def test_le_plan_api_peut_lire_et_ecrire(tenant):
    with tenant_scoped_session(tenant_id=tenant) as db:
        db.add(_alerte(tenant))
        db.flush()
        assert db.query(Alert).count() == 1


def test_deux_alertes_ouvertes_pour_la_meme_condition_sont_refusees(tenant):
    """LE test. La déduplication est une contrainte de la base, pas une convention."""
    with tenant_scoped_session(tenant_id=tenant) as db:
        db.add(_alerte(tenant))
        db.flush()
        db.add(_alerte(tenant))
        with pytest.raises(Exception):
            db.flush()
        db.rollback()


def test_une_alerte_fermee_ne_bloque_pas_la_reouverture(tenant):
    """Un échec qui disparaît puis revient doit pouvoir rouvrir une alerte : l'index est
    PARTIEL (WHERE closed_at IS NULL), sinon on ne pourrait jamais réarmer."""
    with tenant_scoped_session(tenant_id=tenant) as db:
        db.add(_alerte(tenant, closed_at=datetime.now(timezone.utc)))
        db.flush()
        db.add(_alerte(tenant))          # nouvelle, ouverte : doit passer
        db.flush()
        assert db.query(Alert).filter(Alert.closed_at.is_(None)).count() == 1


def test_rls_est_active_et_forcee():
    with get_session() as db:
        row = db.execute(text(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname='alert'"
        )).one()
    assert row == (True, True)


def test_index_unique_partiel_existe():
    with get_session() as db:
        rows = db.execute(text(
            "SELECT indexdef FROM pg_indexes "
            "WHERE tablename='alert' AND indexname='ux_alert_ouverte'"
        )).scalar()
    assert rows and "closed_at IS NULL" in rows
