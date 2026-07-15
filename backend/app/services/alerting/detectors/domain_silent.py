"""Le domaine recevait des rapports, et n'en reçoit plus.

Le signal MUET. C'est un `_dmarc` cassé, un TXT supprimé par un client qui « faisait le
ménage », un domaine qui a changé d'hébergeur. **Aucun écran ne le montrera jamais,
puisqu'il ne s'y passe rien** — c'est très exactement la panne que ce produit existe pour
attraper, et la seule qu'on ne peut pas voir en regardant.

À distinguer de `never_reported` : un domaine qui n'a jamais parlé n'est pas devenu
silencieux. Les confondre enverrait l'exploitant chercher une panne là où il n'y a qu'une
procédure jamais terminée — deux gestes complètement différents.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func

from app.config import settings
from app.db.models import Report
from app.services.alerting.base import CRITICAL, Condition, register_detector


@register_detector("domain_silent")
def detect(db, tenant) -> list[Condition]:
    if tenant.status != "active":
        return []

    # Aucun filtre tenant_id : la RLS scope la session (CLAUDE.md).
    dernier = db.query(func.max(Report.created_at)).scalar()
    if dernier is None:
        return []          # il n'a jamais parlé : c'est never_reported, pas du silence

    silence = datetime.now(timezone.utc) - dernier
    if silence < timedelta(days=settings.alert_silence_days):
        return []

    return [Condition(
        kind="domain_silent", dedup_key="", severity=CRITICAL,
        payload={"domain": tenant.domain, "silence_days": silence.days,
                 "last_report_at": dernier.isoformat()},
    )]
