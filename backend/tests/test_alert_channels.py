"""La sélection du canal d'alerte. Le systeme d'alertes ne connait qu'un contrat
`envoyer(event, alert, tenant)` ; QUEL canal l'implemente est un choix de configuration.
"""
import pytest

from app.services.alerting import channels
from app.services.alerting.channels import webhook
from app.services.alerting.channels.base import CanalIndisponible


def test_defaut_est_le_webhook(monkeypatch):
    monkeypatch.setattr(channels.settings, "alert_channel", "webhook")
    assert channels.get_channel() is webhook


def test_desk365_selectionnable(monkeypatch):
    from app.services.alerting.channels import desk365
    monkeypatch.setattr(channels.settings, "alert_channel", "desk365")
    assert channels.get_channel() is desk365


def test_canal_inconnu_leve_clairement(monkeypatch):
    """Un ALERT_CHANNEL fautif doit lever bruyamment, pas se rabattre en silence sur un
    canal par defaut : un canal muet choisi par erreur, c'est exactement la panne que ce
    produit combat."""
    monkeypatch.setattr(channels.settings, "alert_channel", "pigeon-voyageur")
    with pytest.raises(ValueError, match="ALERT_CHANNEL"):
        channels.get_channel()


def test_webhook_indisponible_est_un_canal_indisponible():
    """tasks.py attrape `CanalIndisponible` : l'exception du webhook doit en heriter,
    sinon les pannes de webhook ne seraient plus retentees apres le refactor."""
    assert issubclass(webhook.WebhookIndisponible, CanalIndisponible)
