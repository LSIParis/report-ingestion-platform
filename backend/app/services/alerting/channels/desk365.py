"""Canal d'alerte Desk365 : une alerte critique ouvre un ticket, sa fermeture l'annote.

Tout le couplage a Desk365 est ICI. Le reste du systeme d'alertes ne connait que le
contrat `envoyer(event, alert, tenant) -> bool` (voir channels/base.py).

Regles heritees du systeme d'alertes, inchangees :
 - Non configure (pas de cle API) != silencieux : on journalise, on ne cree rien.
 - L'API en panne leve `CanalIndisponible` -> Celery retente. On perd une notification,
   jamais une alerte.

Seules les alertes CRITIQUES ouvrent un ticket : un avertissement (tls_failure en mode
testing, ou rien n'est bloque) reste visible sur la page Alertes sans inonder le support
d'un ticket Urgent. La selection porte sur la SEVERITE, jamais sur le kind -- un futur
detecteur critique obtient un ticket sans code en plus.

On ANNOTE a la fermeture, on ne CLOTURE jamais : une alerte qui se ferme puis rouvre
ballotterait le ticket, et cloturer sans qu'un humain ait verifie est le contraire de ce
qu'un support veut.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime, timezone

import structlog

from app.config import settings
from app.services.alerting.channels.base import CanalIndisponible

log = structlog.get_logger()

TIMEOUT = 15.0

# Vise le meme registre que la page Alertes (frontend/src/pages/Alerts.tsx) : le
# technicien doit reconnaitre de quoi on parle. Mais ces libelles sont independants de
# ceux du frontend -- aucune source commune, aucune synchronisation automatique. Si on
# modifie l'un, penser a verifier l'autre.
_NATURES = {
    "never_reported": "aucun rapport reçu depuis l'ajout du domaine",
    "domain_silent": "les rapports ont cessé d'arriver",
    "tls_failure": "échec de chiffrement TLS",
}


def _sujet(alert, tenant) -> str:
    quoi = _NATURES.get(alert.kind, alert.kind)
    return f"[DMARC] {tenant.domain} — {quoi}"


def _description(alert, tenant) -> str:
    lignes = [
        f"Domaine : {tenant.domain}",
        f"Type d'alerte : {alert.kind} ({alert.severity})",
        "",
        "Ce que la plateforme a constaté :",
    ]
    for cle, valeur in (alert.payload or {}).items():
        lignes.append(f"  - {cle} : {valeur}")
    lignes += ["", "Détail et historique : page Alertes de la plateforme de rapports DMARC."]
    return "\n".join(lignes)


def _call(method: str, path: str, body: dict) -> dict:
    """Un appel à l'API Desk365. Toute erreur réseau/HTTP/illisible -> CanalIndisponible.

    Point d'étranglement UNIQUE : les tests le moquent (ils prouvent la logique), et le
    test réel de bout en bout valide le format sur le fil.
    """
    url = f"{settings.desk365_base_url.rstrip('/')}/{path.lstrip('/')}"
    data = json.dumps(body).encode()
    req = urllib.request.Request(            # noqa: S310 — URL d'exploitation, configurée
        url, data=data, method=method,
        headers={"Content-Type": "application/json",
                 "Authorization": settings.desk365_api_key},
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:   # noqa: S310
            raw = r.read().decode("utf-8", "replace")
    except (OSError, urllib.error.URLError) as exc:
        raise CanalIndisponible(f"Desk365 {path} : {exc}") from exc
    try:
        return json.loads(raw) if raw.strip() else {}
    except ValueError as exc:
        raise CanalIndisponible(f"Desk365 {path} : réponse illisible") from exc


def _creer_ticket(alert, tenant) -> str:
    """Crée le ticket, renvoie son numéro. Lève si la réponse n'est pas exploitable."""
    body = {
        "email": settings.desk365_requester_email,
        "status": settings.desk365_status,
        "subject": _sujet(alert, tenant),
        "description": _description(alert, tenant),
        "group": settings.desk365_group,
        "priority": settings.desk365_priority,
        "category": settings.desk365_category,
        "sub_category": settings.desk365_subcategory,
    }
    resp = _call("POST", "tickets/create", body)
    numero = resp.get("ticket_number") or resp.get("ticket_id") or resp.get("id")
    if not numero:
        # Sans numero, impossible d'annoter le ticket a la fermeture : on retente plutot
        # que de poser un external_ref faux.
        raise CanalIndisponible(f"Desk365 create : pas de numéro de ticket ({resp!r})")
    return str(numero)


def _ajouter_note(ticket_ref: str, alert) -> None:
    quand = (alert.closed_at or datetime.now(timezone.utc)).isoformat()
    # ticket_number est un PARAMETRE DE REQUETE dans l'URL, pas un champ du corps JSON
    # (confirme reel : le mettre dans le corps donne 405).
    _call("POST", f"tickets/add_note?ticket_number={ticket_ref}", {
        "body": f"Condition résolue le {quand}. Vérifiez puis clôturez ce ticket.",
        "private_note": 1,
    })


def envoyer(event: str, alert, tenant) -> bool:
    if not settings.desk365_api_key or not settings.desk365_base_url:
        log.warning("alerting.desk365_non_configure", alert_event=event,
                    kind=alert.kind, domain=tenant.domain)
        return False

    if event == "opened":
        if alert.severity != "critical":
            return False        # un avertissement ne cree pas de ticket
        alert.external_ref = _creer_ticket(alert, tenant)   # commit par l'appelant
        log.info("alerting.desk365_ticket_cree", ticket=alert.external_ref,
                 kind=alert.kind, domain=tenant.domain)
        return True

    # event == "closed"
    if not alert.external_ref:
        return False            # aucun ticket a annoter
    _ajouter_note(alert.external_ref, alert)
    log.info("alerting.desk365_note_ajoutee", ticket=alert.external_ref,
             domain=tenant.domain)
    return True
