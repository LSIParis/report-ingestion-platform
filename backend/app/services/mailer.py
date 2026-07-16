"""Envoi d'e-mail sortant (SMTP), source UNIQUE.

Utilise pour la verification de changement d'e-mail (et, au cycle 2, le canal d'alerte
e-mail). Toute impossibilite d'envoi -- SMTP non configure, erreur reseau/SMTP -- leve
EmailNonEnvoye : l'appelant la traduit en erreur claire, jamais un plantage silencieux.
Le corps n'est jamais journalise (il peut contenir un code).
"""
from __future__ import annotations

import smtplib
from email.message import EmailMessage

import structlog

from app.config import settings

log = structlog.get_logger()


class EmailNonEnvoye(Exception):
    """L'e-mail n'a pas pu etre envoye (SMTP non configure ou echec de l'envoi)."""


def send_email(to: str, subject: str, body: str) -> None:
    if not settings.smtp_host:
        raise EmailNonEnvoye("SMTP non configure (smtp_host vide)")

    msg = EmailMessage()
    msg["From"] = settings.smtp_from
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
            smtp.starttls()
            if settings.smtp_user:
                smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(msg)
    except (smtplib.SMTPException, OSError) as exc:
        log.warning("email_non_envoye", to=to, error=str(exc))
        raise EmailNonEnvoye(f"echec SMTP : {exc}") from exc
