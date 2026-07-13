"""Webhook d'ingestion entrante (Amazon SES via SNS).

Flux prod : règle de réception SES → action S3 (stocke le .eml) + action SNS
(notifie) → SNS POST sur cet endpoint. On vérifie la signature SNS, on confirme
l'abonnement le cas échéant, puis on lit le .eml depuis S3 et on le passe au
MÊME IngestionService que l'IMAP. Endpoint PUBLIC (AWS) → sécurité = signature SNS.

NB : SES écrit dans S3 AWS. Activer ce chemin implique une config S3 AWS
(S3_ENDPOINT vide/AWS + credentials IAM) plutôt que MinIO.
"""
from __future__ import annotations

import json

import structlog
from fastapi import APIRouter, HTTPException, Request, status

from app.config import settings
from app.ingestion import ses_handler, sns
from app.ingestion.service import IngestionService
from app.storage import ObjectStore

log = structlog.get_logger()
router = APIRouter(prefix="/ingest", tags=["ingest"])

_store = ObjectStore.from_settings(settings)
_service = IngestionService(_store)


@router.post("/ses")
async def ses_inbound(request: Request):
    try:
        envelope = json.loads(await request.body())
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "corps non JSON")

    try:
        sns.verify(envelope)
    except sns.SnsError as exc:
        log.warning("sns.verify_failed", error=str(exc))
        raise HTTPException(status.HTTP_403_FORBIDDEN, "signature SNS invalide")

    msg_type = envelope.get("Type")

    if msg_type == "SubscriptionConfirmation":
        sns.confirm_subscription(envelope["SubscribeURL"])
        log.info("sns.subscription_confirmed", topic=envelope.get("TopicArn"))
        return {"status": "subscription confirmed"}

    if msg_type == "Notification":
        event = json.loads(envelope["Message"])          # événement SES (mail + receipt)
        ses_handler.handle_ses_notification(event, _service, _store)
        return {"status": "ok"}

    return {"status": "ignored", "type": msg_type}
