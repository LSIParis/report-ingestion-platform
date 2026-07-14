"""Le réconciliateur : il ouvre, il ferme, et surtout il NE ROUVRE PAS.

C'est ici que la déduplication se joue — et le point du design est justement qu'elle ne se
« joue » pas : elle est une conséquence du modèle. Une condition déjà ouverte n'est pas
rouverte, donc pas renotifiée. On n'a aucune règle « ne pas spammer » à écrire.
"""
import uuid

import pytest

from app.db.models import Alert, Tenant
from app.db.session import get_session, tenant_scoped_session
from app.services.alerting.base import Condition
from app.services.alerting.reconciler import reconcile


@pytest.fixture
def domaine():
    with get_session() as db:
        t = Tenant(domain=f"rec-{uuid.uuid4().hex[:8]}.test", name="Rec")
        db.add(t)
        db.commit()
        tid = str(t.id)

    yield tid

    with get_session() as db:
        db.query(Alert).filter_by(tenant_id=tid).delete()
        db.query(Tenant).filter_by(id=tid).delete()
        db.commit()


@pytest.fixture
def conditions(monkeypatch):
    """Pilote ce que les détecteurs « voient ». On teste le réconciliateur, pas eux."""
    from app.services.alerting import reconciler

    courantes: list[Condition] = []
    monkeypatch.setattr(reconciler, "all_conditions", lambda db, tenant: list(courantes))
    return courantes


def _reconcilier(tid):
    with tenant_scoped_session(tenant_id=tid) as db:
        tenant = db.get(Tenant, tid)
        res = reconcile(db, tenant)
        return [a.kind for a in res.opened], [a.kind for a in res.closed]


def _ouvertes(tid):
    with tenant_scoped_session(tenant_id=tid) as db:
        return db.query(Alert).filter(Alert.closed_at.is_(None)).all()


C = Condition(kind="tls_failure", dedup_key="k1", severity="warning", payload={"a": 1})


def test_une_condition_vraie_ouvre_une_alerte(domaine, conditions):
    conditions.append(C)

    opened, closed = _reconcilier(domaine)

    assert opened == ["tls_failure"] and closed == []
    assert len(_ouvertes(domaine)) == 1


def test_la_meme_condition_ne_rouvre_RIEN(domaine, conditions):
    """LE test. Sans lui, on renotifie l'exploitant tous les jours pour le même problème,
    et il finit par ignorer nos alertes — ce qui les rend pires qu'inutiles."""
    conditions.append(C)
    _reconcilier(domaine)

    opened, closed = _reconcilier(domaine)      # second passage, rien n'a changé

    assert opened == [] and closed == []
    assert len(_ouvertes(domaine)) == 1


def test_une_condition_disparue_ferme_son_alerte(domaine, conditions):
    conditions.append(C)
    _reconcilier(domaine)

    conditions.clear()
    opened, closed = _reconcilier(domaine)

    assert opened == [] and closed == ["tls_failure"]
    assert _ouvertes(domaine) == []


def test_une_condition_qui_revient_ROUVRE_une_alerte_neuve(domaine, conditions):
    """Le réarmement. Un échec qui disparaît puis revient est un NOUVEL événement : il
    mérite une nouvelle alerte, avec sa propre date. C'est ce que permet le caractère
    PARTIEL de l'index unique."""
    conditions.append(C)
    _reconcilier(domaine)
    conditions.clear()
    _reconcilier(domaine)

    conditions.append(C)
    opened, _ = _reconcilier(domaine)

    assert opened == ["tls_failure"]
    with tenant_scoped_session(tenant_id=domaine) as db:
        assert db.query(Alert).count() == 2          # l'ancienne, fermée + la neuve
        assert db.query(Alert).filter(Alert.closed_at.is_(None)).count() == 1


def test_deux_conditions_du_meme_kind_sont_deux_alertes(domaine, conditions):
    """Deux échecs TLS différents (deux MTA distincts) sont deux problèmes distincts : la
    dedup_key les sépare."""
    conditions.append(C)
    conditions.append(Condition(kind="tls_failure", dedup_key="k2", severity="warning"))

    opened, _ = _reconcilier(domaine)

    assert len(opened) == 2
    assert len(_ouvertes(domaine)) == 2


def test_le_reconciliateur_est_idempotent(domaine, conditions):
    """On peut donc le lancer aussi souvent qu'on veut — à l'ingestion ET au balayage —
    sans se demander si on va spammer. C'est ce qui rend le crochet d'ingestion gratuit."""
    conditions.append(C)

    for _ in range(5):
        _reconcilier(domaine)

    with tenant_scoped_session(tenant_id=domaine) as db:
        assert db.query(Alert).count() == 1


def test_la_severite_est_enregistree(domaine, conditions):
    conditions.append(Condition(kind="tls_failure", dedup_key="k",
                                severity="critical", payload={"x": 1}))

    _reconcilier(domaine)

    a = _ouvertes(domaine)[0]
    assert a.severity == "critical"
    assert a.payload == {"x": 1}
    assert a.opened_at is not None and a.closed_at is None
