"""Le canal webhook.

Deux règles non négociables :
 - **il ne casse jamais le flux métier** (même règle que `audit()`) ;
 - **non configuré ≠ silencieux** : une URL vide est journalisée, jamais avalée.
"""
import http.server
import json
import threading
import uuid
from datetime import datetime, timezone

import pytest
import structlog.testing

from app.services.alerting.channels import webhook


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


def test_url_non_configuree_ne_leve_pas_mais_ne_se_tait_pas(monkeypatch):
    """On n'avale JAMAIS une alerte en silence : l'absence d'envoi est journalisée.

    `caplog` de pytest ne convient pas ici : il patche les handlers de la bibliothèque
    standard `logging`, or ce dépôt n'appelle nulle part `structlog.configure(...)` pour
    router structlog vers `logging` -- structlog tourne donc avec sa config par défaut
    (un PrintLogger qui écrit sur stdout) et `caplog` ne verrait rien passer, que le
    `log.warning(...)` soit présent ou non. `structlog.testing.capture_logs()` intercepte
    directement la chaîne de processors de structlog, quelle que soit sa configuration :
    c'est le seul moyen qui prouve réellement l'émission de l'événement ici.
    """
    monkeypatch.setattr(webhook.settings, "alert_webhook_url", "")

    with structlog.testing.capture_logs() as logs:
        envoye = webhook.envoyer("opened", _Alerte(), _Tenant())

    assert envoye is False          # rien n'a été envoyé, et on le dit
    assert any(
        e["event"] == "alerting.webhook_non_configure" and e["log_level"] == "warning"
        for e in logs
    ), "l'absence d'envoi doit être journalisée en warning, pas seulement retournée"


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


def test_un_serveur_qui_repond_500_leve_par_le_vrai_chemin(monkeypatch):
    """Exerce le vrai chemin de production, sans moquer `_post` : un serveur qui répond
    500 doit produire un `WebhookIndisponible`.

    On monte un vrai `http.server` local plutôt que de simuler `urllib.error.HTTPError`,
    pour prouver ce qui se passe réellement avec `urllib.request.urlopen` : il lève
    `HTTPError` (sous-classe d'`OSError`) *avant* de renvoyer un statut >= 400. C'est
    précisément pourquoi la ligne `if status >= 400` de `envoyer()` est inatteignable en
    usage réel -- ce test passe par le `except (OSError, urllib.error.URLError)` juste
    au-dessus, jamais par cette ligne.
    """
    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(500)
            self.end_headers()

        def log_message(self, *args):
            pass  # silence le log par défaut de http.server pendant les tests

    serveur = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=serveur.handle_request)  # une seule requête suffit
    thread.start()
    try:
        url = f"http://127.0.0.1:{serveur.server_port}/hook"
        monkeypatch.setattr(webhook.settings, "alert_webhook_url", url)

        with pytest.raises(webhook.WebhookIndisponible):
            webhook.envoyer("opened", _Alerte(), _Tenant())
    finally:
        thread.join(timeout=5)
        serveur.server_close()
