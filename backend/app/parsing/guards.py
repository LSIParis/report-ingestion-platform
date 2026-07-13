"""Gardes de sécurité appliquées entre le parsing et la normalisation.

Fonctions pures (aucune I/O) : elles prennent un ParseResult et le contexte tenant,
et renvoient le ParseResult à persister — éventuellement vidé de ses lignes.
"""
from __future__ import annotations

from app.parsing.base import ParseResult


def guard_report_domain(parsed: ParseResult, tenant_domain: str | None) -> ParseResult:
    """Recoupe le tenant résolu avec le domaine que le rapport déclare lui-même
    (DMARC : `policy_published/domain`).

    Le tenant est résolu depuis le **sujet** du mail — or le sujet est contrôlé par
    l'expéditeur, et la boîte de collecte est ouverte : n'importe qui peut forger
    « Report Domain: client-a.com » pour faire écrire ses données dans le tenant de
    client-a. On exige donc que le contenu confirme le sujet. Au moindre désaccord,
    AUCUNE ligne n'est écrite (invariant §6 : on ne devine jamais).

    Les formats non auto-descriptifs (CSV/XLSX/PDF) ne déclarent pas de domaine :
    la garde les laisse passer inchangés.
    """
    declared = parsed.metadata.get("policy_domain")
    if not declared or not tenant_domain:
        return parsed

    declared, expected = declared.lower(), tenant_domain.lower()
    # Un sous-domaine du tenant est légitime (il peut publier sa propre politique).
    # Comparaison sur le point séparateur : 'notacme.com' ne passe pas pour 'acme.com'.
    if declared == expected or declared.endswith(f".{expected}"):
        return parsed

    return ParseResult(
        status="failed", rows=[],
        errors=[{"code": "DMARC_DOMAIN_MISMATCH", "severity": "fatal",
                 "message": (f"Le rapport concerne '{declared}' mais l'e-mail a été "
                             f"résolu vers le tenant '{expected}' — rejeté.")}],
        metadata=parsed.metadata,
    )
