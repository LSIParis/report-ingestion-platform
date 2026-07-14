"""Le balayage et la notification.

Le test qui compte est `test_le_balayage_scope_bien_chaque_tenant` : les détecteurs
n'ont aucun filtre tenant_id applicatif, ils comptent sur la RLS. Les faire tourner sur la
session du worker (qui BYPASSE la RLS) leur ferait voir TOUS les tenants, et ouvrirait les
alertes d'un client sur le domaine d'un autre. Avec un seul tenant en développement, ce
bug est INVISIBLE.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.db.models import Alert, Tenant
from app.db.session import get_session
from app.workers import tasks


@pytest.fixture
def deux_domaines():
    """A et B, tous deux vieux et sans aucun rapport → chacun doit lever never_reported,
    et UNIQUEMENT le sien."""
    vieux = datetime.now(timezone.utc) - timedelta(days=30)
    with get_session() as db:
        a = Tenant(domain=f"sweep-a-{uuid.uuid4().hex[:6]}.test", name="A", created_at=vieux)
        b = Tenant(domain=f"sweep-b-{uuid.uuid4().hex[:6]}.test", name="B", created_at=vieux)
        db.add_all([a, b])
        db.commit()
        ids = (str(a.id), str(b.id))

    yield ids

    with get_session() as db:
        for tid in ids:
            db.query(Alert).filter_by(tenant_id=tid).delete()
            db.query(Tenant).filter_by(id=tid).delete()
        db.commit()


def _alertes(tid):
    with get_session() as db:
        return db.query(Alert).filter_by(tenant_id=tid).all()


def test_le_balayage_ouvre_une_alerte_par_domaine(deux_domaines, monkeypatch):
    monkeypatch.setattr(tasks.notify_alert, "delay", lambda *a, **k: None)

    tasks.sweep_alerts()

    for tid in deux_domaines:
        kinds = [a.kind for a in _alertes(tid)]
        assert kinds == ["never_reported"]


def test_le_balayage_scope_bien_chaque_tenant(deux_domaines, monkeypatch):
    """Chaque alerte porte le tenant_id du domaine qu'elle concerne — et un seul.

    Si le balayage utilisait la session du worker (BYPASSRLS), les détecteurs verraient
    tous les tenants et poseraient des alertes croisées.
    """
    monkeypatch.setattr(tasks.notify_alert, "delay", lambda *a, **k: None)
    tid_a, tid_b = deux_domaines

    tasks.sweep_alerts()

    a = _alertes(tid_a)
    assert len(a) == 1
    assert str(a[0].tenant_id) == tid_a
    assert a[0].payload["domain"].startswith("sweep-a-")   # PAS le domaine de B


def test_le_balayage_ignore_un_domaine_suspendu(deux_domaines, monkeypatch):
    monkeypatch.setattr(tasks.notify_alert, "delay", lambda *a, **k: None)
    tid_a, tid_b = deux_domaines
    with get_session() as db:
        db.get(Tenant, tid_b).status = "suspended"
        db.commit()

    tasks.sweep_alerts()

    assert _alertes(tid_b) == []          # un domaine coupé n'a pas à alerter
    assert len(_alertes(tid_a)) == 1


def test_le_balayage_est_idempotent(deux_domaines, monkeypatch):
    monkeypatch.setattr(tasks.notify_alert, "delay", lambda *a, **k: None)

    tasks.sweep_alerts()
    tasks.sweep_alerts()
    tasks.sweep_alerts()

    for tid in deux_domaines:
        assert len(_alertes(tid)) == 1     # pas de doublon, pas de renotification


def test_un_webhook_en_panne_ne_casse_pas_le_balayage(deux_domaines, monkeypatch):
    """La base est la source de vérité. Un canal en panne fait perdre une NOTIFICATION,
    jamais une ALERTE."""
    def _explose(*a, **k):
        raise RuntimeError("redis indisponible")

    monkeypatch.setattr(tasks.notify_alert, "delay", _explose)

    tasks.sweep_alerts()                   # ne doit pas lever

    for tid in deux_domaines:
        assert len(_alertes(tid)) == 1     # l'alerte est bien en base
