"""Le domaine n'a JAMAIS reçu le moindre rapport.

La plus précieuse des trois alertes. Elle attrape le client qu'on croit protégé et qui ne
l'est pas : celui dont la procédure d'onboarding n'a jamais été terminée — un `_dmarc`
jamais publié, ou publié de travers. Aujourd'hui, rien dans l'application ne le distingue
d'un client tranquille : les deux écrans sont vides.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.config import settings
from app.db.models import Report
from app.services.alerting.base import CRITICAL, Condition, register_detector


@register_detector("never_reported")
def detect(db, tenant) -> list[Condition]:
    if tenant.status != "active":
        return []

    age = datetime.now(timezone.utc) - tenant.created_at
    if age < timedelta(days=settings.alert_onboarding_grace_days):
        return []          # on lui laisse le temps de publier son DMARC

    # Aucun filtre tenant_id : la RLS scope la session (CLAUDE.md).
    if db.query(Report.id).first() is not None:
        return []

    return [Condition(
        kind="never_reported", dedup_key="", severity=CRITICAL,
        payload={"domain": tenant.domain, "age_days": age.days,
                 "created_at": tenant.created_at.isoformat()},
    )]
