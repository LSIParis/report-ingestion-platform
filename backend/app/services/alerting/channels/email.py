"""Canal d'alerte e-mail : envoie l'alerte au(x) destinataire(s) du tenant (alert_email).

Meme contrat que les autres canaux (channels/base.py) : True si envoye, False si non
configure (pas d'alert_email sur ce tenant -- journalise, jamais un silence muet), leve
EmailIndisponible (sous-classe de CanalIndisponible) si l'envoi echoue -> Celery retente.
Corps texte simple ; meme famille de libelles que le canal Desk365 (tables independantes).
"""
from __future__ import annotations

import structlog

from app.services.alerting.channels.base import CanalIndisponible
from app.services.mailer import EmailNonEnvoye, send_email

log = structlog.get_logger()

_NATURES = {
    "never_reported": "aucun rapport reçu depuis l'ajout du domaine",
    "domain_silent": "les rapports ont cessé d'arriver",
    "tls_failure": "échec de chiffrement TLS",
}


class EmailIndisponible(CanalIndisponible):
    """L'envoi de l'alerte par e-mail a echoue. Celery retentera."""


def _destinataires(tenant) -> list[str]:
    return [a.strip() for a in (tenant.alert_email or "").split(",") if a.strip()]


def _sujet(alert, tenant) -> str:
    quoi = _NATURES.get(alert.kind, alert.kind)
    return f"[DMARC] {tenant.domain} — {quoi}"


def _corps(event: str, alert, tenant) -> str:
    etat = "Alerte OUVERTE" if event == "opened" else "Alerte RÉSOLUE"
    lignes = [
        etat,
        "",
        f"Domaine : {tenant.domain}",
        f"Type : {alert.kind} ({alert.severity})",
        "",
        "Ce que la plateforme a constaté :",
    ]
    for cle, valeur in (alert.payload or {}).items():
        lignes.append(f"  - {cle} : {valeur}")
    lignes += ["", "Détail et historique : page Alertes de la plateforme."]
    return "\n".join(lignes)


def envoyer(event: str, alert, tenant) -> bool:
    dests = _destinataires(tenant)
    if not dests:
        log.warning("alerting.email_non_configure", alert_event=event,
                    kind=alert.kind, domain=tenant.domain)
        return False

    sujet = _sujet(alert, tenant)
    corps = _corps(event, alert, tenant)
    try:
        for adresse in dests:
            send_email(adresse, sujet, corps)
    except EmailNonEnvoye as exc:
        raise EmailIndisponible(str(exc)) from exc

    log.info("alerting.email_envoye", alert_event=event, kind=alert.kind,
             domain=tenant.domain, destinataires=len(dests))
    return True
