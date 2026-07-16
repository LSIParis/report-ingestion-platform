"""Canal d'alerte e-mail : envoie a tenant.alert_email. send_email est MOQUE."""
from types import SimpleNamespace

import pytest

from app.services.alerting.channels import email as canal
from app.services.alerting.channels.base import CanalIndisponible


def _alert():
    return SimpleNamespace(kind="tls_failure", severity="critical",
                           payload={"sessions": 12, "mx": "mx.exemple.fr"})


def _tenant(alert_email):
    return SimpleNamespace(domain="exemple.fr", alert_email=alert_email)


def test_pas_de_destinataire_renvoie_false(monkeypatch):
    appels = []
    monkeypatch.setattr(canal, "send_email", lambda *a, **k: appels.append(a))
    assert canal.envoyer("opened", _alert(), _tenant(None)) is False
    assert appels == []


def test_envoi_ouverture(monkeypatch):
    vu = {}

    def faux(to, subject, body):
        vu["to"] = to
        vu["subject"] = subject
        vu["body"] = body

    monkeypatch.setattr(canal, "send_email", faux)
    assert canal.envoyer("opened", _alert(), _tenant("ops@exemple.fr")) is True
    assert vu["to"] == "ops@exemple.fr"
    assert "exemple.fr" in vu["subject"]
    assert "OUVERTE" in vu["body"]
    assert "tls_failure" in vu["body"]


def test_corps_resolue_a_la_fermeture(monkeypatch):
    vu = {}

    def faux(to, subject, body):
        vu["body"] = body

    monkeypatch.setattr(canal, "send_email", faux)
    canal.envoyer("closed", _alert(), _tenant("ops@exemple.fr"))
    assert "RÉSOLUE" in vu["body"]


def test_plusieurs_destinataires(monkeypatch):
    tos = []

    def faux(to, subject, body):
        tos.append(to)

    monkeypatch.setattr(canal, "send_email", faux)
    canal.envoyer("opened", _alert(), _tenant("a@x.fr, b@y.fr"))
    assert tos == ["a@x.fr", "b@y.fr"]


def test_echec_smtp_leve_canalindisponible(monkeypatch):
    from app.services.mailer import EmailNonEnvoye

    def echoue(*a, **k):
        raise EmailNonEnvoye("smtp ko")

    monkeypatch.setattr(canal, "send_email", echoue)
    with pytest.raises(CanalIndisponible):
        canal.envoyer("opened", _alert(), _tenant("ops@exemple.fr"))


def test_get_channel_email(monkeypatch):
    from app.config import settings
    from app.services.alerting.channels import get_channel

    monkeypatch.setattr(settings, "alert_channel", "email")
    assert get_channel() is canal
