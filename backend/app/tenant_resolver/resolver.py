from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

import structlog

from app.db.models import Email, TenantMatchingRule
from app.db.session import get_session

log = structlog.get_logger()

FUZZY_THRESHOLD = 0.88


@dataclass(frozen=True)
class TenantMatch:
    tenant_id: str | None
    method: str
    confidence: float


class TenantResolverService:
    """Cascade fail-safe. Aucune assignation risquée : si rien de sûr → quarantaine.
    Les règles sont EN BASE (configurable), pas codées en dur."""

    def resolve(self, email_id: str) -> TenantMatch:
        with get_session() as session:
            em = session.query(Email).filter_by(id=email_id).one()
            rules = (
                session.query(TenantMatchingRule)
                .filter_by(is_active=True)
                .order_by(TenantMatchingRule.priority.asc())
                .all()
            )
            match = self._match(em.from_address, em.subject, rules)

            if match.tenant_id:
                em.tenant_id = match.tenant_id
                em.status = "tenant_resolved"
                em.resolved_by = match.method
                log.info("tenant.resolved", email_id=email_id,
                         tenant_id=match.tenant_id, method=match.method,
                         confidence=match.confidence)
            else:
                em.status = "needs_review"
                log.warning("tenant.quarantined", email_id=email_id,
                            subject=em.subject, from_addr=em.from_address)
            session.commit()
            return match

    def _match(self, from_addr: str, subject: str,
               rules: list[TenantMatchingRule]) -> TenantMatch:
        from_addr = (from_addr or "").lower()
        subject = subject or ""

        for r in (x for x in rules if x.rule_type == "sender"):
            if r.pattern.lower() in from_addr:
                return TenantMatch(str(r.tenant_id), "sender", 1.0)

        for r in (x for x in rules if x.rule_type == "subject_regex"):
            if re.search(r.pattern, subject, re.IGNORECASE):
                return TenantMatch(str(r.tenant_id), "subject_regex", 0.95)

        for r in (x for x in rules if x.rule_type == "keyword"):
            if r.pattern.lower() in subject.lower():
                return TenantMatch(str(r.tenant_id), "keyword", 0.85)

        best = TenantMatch(None, "none", 0.0)
        for r in (x for x in rules if x.rule_type == "alias"):
            score = SequenceMatcher(None, r.pattern.lower(), subject.lower()).ratio()
            if score > best.confidence:
                best = TenantMatch(str(r.tenant_id), "alias", score)
        if best.confidence >= FUZZY_THRESHOLD:
            return best

        return TenantMatch(None, "none", 0.0)
