"""Le canal d'alerte : un POST JSON sur une URL générique.

**Générique, délibérément.** Pas de blocs Slack, pas de carte Teams : une URL, un corps
JSON. n8n, un script, un endpoint à vous — et changer d'outil demain ne demande pas de
recoder. Le couplage à un fournisseur se paie toujours deux fois.

Deux règles non négociables :

 - **Non configuré ≠ silencieux.** Une URL vide ne fait rien, mais le JOURNALISE. On
   n'avale jamais une alerte sans le dire — ce serait reproduire exactement la panne que ce
   produit combat : quelque chose qui ne se passe pas, et que personne ne voit.

 - **L'envoi ne casse jamais le flux métier.** Ce module lève quand l'envoi échoue (pour
   que Celery retente), mais son APPELANT — le pipeline — ne doit jamais tomber pour ça.
   La base est la source de vérité : l'alerte est ouverte, elle le reste, l'écran la
   montre. Un webhook en panne fait perdre une notification, jamais une alerte.

Pas de dépendance HTTP : `urllib.request` de la bibliothèque standard suffit, comme dans
`services/onboarding.py`.
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

TIMEOUT = 10.0


class WebhookIndisponible(CanalIndisponible):
    """L'URL est configurée mais l'envoi a échoué. Celery retentera."""


def corps(event: str, alert, tenant) -> dict:
    """Le JSON envoyé. Sérialisable tel quel — aucun objet exotique."""
    return {
        "event": event,                       # "opened" | "closed"
        "at": datetime.now(timezone.utc).isoformat(),
        "alert": {
            "id": str(alert.id),
            "kind": alert.kind,
            "severity": alert.severity,
            "dedup_key": alert.dedup_key,
            "opened_at": alert.opened_at.isoformat() if alert.opened_at else None,
            "closed_at": alert.closed_at.isoformat() if alert.closed_at else None,
            "payload": alert.payload,
        },
        "tenant": {"id": str(tenant.id), "domain": tenant.domain},
    }


def _post(url: str, data: bytes, timeout: float) -> int:
    req = urllib.request.Request(          # noqa: S310 — URL d'exploitation, configurée
        url, data=data, method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:   # noqa: S310
        return r.status


def envoyer(event: str, alert, tenant) -> bool:
    """Renvoie True si envoyé, False si le canal n'est pas configuré.

    Lève `WebhookIndisponible` si l'URL est configurée mais l'envoi a échoué : c'est la
    tâche Celery qui décide de retenter, pas nous.
    """
    url = settings.alert_webhook_url
    if not url:
        # Jamais un silence muet : on DIT qu'on n'a pas envoyé.
        log.warning("alerting.webhook_non_configure", alert_event=event,
                    kind=alert.kind, domain=tenant.domain)
        return False

    payload = json.dumps(corps(event, alert, tenant)).encode()
    try:
        status = _post(url, payload, TIMEOUT)
    except (OSError, urllib.error.URLError) as exc:
        raise WebhookIndisponible(str(exc)) from exc

    # Garde de défense en profondeur, normalement inatteignable : en usage réel,
    # `urlopen` lève déjà `HTTPError` (sous-classe d'`OSError`) pour tout statut >= 400,
    # et c'est le `except` juste au-dessus qui l'attrape -- on n'arrive jamais ici avec
    # un vrai `_post`. Cette ligne ne s'exécute qu'avec un `_post` moqué (tests) ou si le
    # contrat de `_post` change un jour pour renvoyer un statut au lieu de lever. On la
    # garde pour ce cas-là, pas comme chemin normal de traitement des erreurs HTTP.
    if status >= 400:
        raise WebhookIndisponible(f"HTTP {status}")

    log.info("alerting.webhook_envoye", alert_event=event, kind=alert.kind,
             domain=tenant.domain, status=status)
    return True
