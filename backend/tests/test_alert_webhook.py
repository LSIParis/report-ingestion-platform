"""Le canal webhook.

Deux règles non négociables :
 - **il ne casse jamais le flux métier** (même règle que `audit()`) ;
 - **non configuré ≠ silencieux** : une URL vide est journalisée, jamais avalée.
"""
import json
import uuid
from datetime import datetime, timezone

import pytest

from app.db.models import Alert, Tenant  # noqa: F401 — documente le contrat (types reels)
from app.services.alerting import webhook


class _Alerte:
    id = uuid.uuid4()
    kind = "never_reported"
    dedup_key = ""
    severity = "critical"
    payload = {"domain": "client.fr", "age_days": 12}
    opened_at = datetime(2026, 7, 14, 18, 0, tzinfo=timezone.utc)
    closed_at = None


class _Tenant:
    id = uuid.uuid4()
    domain = "client.fr"


def test_le_corps_dit_ce_qui_s_est_passe():
    c = webhook.corps("opened", _Alerte(), _Tenant())

    assert c["event"] == "opened"
    assert c["alert"]["kind"] == "never_reported"
    assert c["alert"]["severity"] == "critical"
    assert c["alert"]["payload"]["domain"] == "client.fr"
    assert c["tenant"]["domain"] == "client.fr"
    assert c["at"]                                  # horodatage de l'envoi
    json.dumps(c)                                   # doit être sérialisable tel quel


def test_url_non_configuree_ne_leve_pas_mais_ne_se_tait_pas(monkeypatch, caplog):
    """On n'avale JAMAIS une alerte en silence : l'absence d'envoi est journalisée."""
    monkeypatch.setattr(webhook.settings, "alert_webhook_url", "")

    envoye = webhook.envoyer("opened", _Alerte(), _Tenant())

    assert envoye is False          # rien n'a été envoyé, et on le dit


def test_envoi_reussi(monkeypatch):
    vu = {}

    def faux_post(url, data, timeout):
        vu["url"] = url
        vu["corps"] = json.loads(data)
        return 200

    monkeypatch.setattr(webhook.settings, "alert_webhook_url", "https://exemple.test/hook")
    monkeypatch.setattr(webhook, "_post", faux_post)

    assert webhook.envoyer("opened", _Alerte(), _Tenant()) is True
    assert vu["url"] == "https://exemple.test/hook"
    assert vu["corps"]["alert"]["kind"] == "never_reported"


def test_un_webhook_en_panne_leve_pour_que_celery_retente(monkeypatch):
    """Il DOIT lever ici — c'est la tâche Celery qui décide de retenter. Ce qui ne doit
    jamais tomber, c'est le PIPELINE : voir le crochet d'ingestion (tâche 5)."""
    def _casse(url, data, timeout):
        raise OSError("connexion refusée")

    monkeypatch.setattr(webhook.settings, "alert_webhook_url", "https://exemple.test/hook")
    monkeypatch.setattr(webhook, "_post", _casse)

    with pytest.raises(webhook.WebhookIndisponible):
        webhook.envoyer("opened", _Alerte(), _Tenant())
