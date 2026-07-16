"""mailer.send_email : envoi SMTP, source unique. On ne parle jamais a un vrai serveur."""
import pytest

from app.services import mailer
from app.services.mailer import EmailNonEnvoye, send_email


def test_smtp_non_configure_leve(monkeypatch):
    monkeypatch.setattr(mailer.settings, "smtp_host", "")
    with pytest.raises(EmailNonEnvoye):
        send_email("x@y.fr", "sujet", "corps")


def test_envoi_appelle_smtp(monkeypatch):
    monkeypatch.setattr(mailer.settings, "smtp_host", "smtp.test")
    monkeypatch.setattr(mailer.settings, "smtp_port", 587)
    monkeypatch.setattr(mailer.settings, "smtp_user", "u")
    monkeypatch.setattr(mailer.settings, "smtp_password", "p")
    monkeypatch.setattr(mailer.settings, "smtp_from", "no-reply@lsiparis.tech")
    vu = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout=10):
            vu["host"] = host
            vu["port"] = port

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self):
            vu["tls"] = True

        def login(self, user, password):
            vu["login"] = user

        def send_message(self, msg):
            vu["to"] = msg["To"]
            vu["from"] = msg["From"]
            vu["subject"] = msg["Subject"]

    monkeypatch.setattr(mailer.smtplib, "SMTP", FakeSMTP)
    send_email("dest@y.fr", "Sujet", "Corps")
    assert vu["host"] == "smtp.test"
    assert vu["tls"] is True
    assert vu["login"] == "u"
    assert vu["to"] == "dest@y.fr"
    assert vu["from"] == "no-reply@lsiparis.tech"
    assert vu["subject"] == "Sujet"


def test_echec_smtp_leve(monkeypatch):
    monkeypatch.setattr(mailer.settings, "smtp_host", "smtp.test")

    class FakeSMTP:
        def __init__(self, *a, **k):
            raise OSError("connexion refusee")

    monkeypatch.setattr(mailer.smtplib, "SMTP", FakeSMTP)
    with pytest.raises(EmailNonEnvoye):
        send_email("x@y.fr", "s", "c")
