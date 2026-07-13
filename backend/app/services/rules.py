"""Validation des règles de résolution de tenant.

La résolution décide À QUI appartient un rapport. Une règle mal formée n'échoue pas :
elle attribue silencieusement les données d'un client à un autre. Ces gardes sont donc
des gardes d'isolation, pas de la validation de formulaire.
"""
from __future__ import annotations

import re

RULE_TYPES = ("sender", "subject_regex", "keyword", "alias")

# Adresses réellement utilisées par les fournisseurs pour envoyer des rapports DMARC.
# Elles servent de sonde : une règle `sender` qui matcherait l'une d'elles capterait les
# rapports de TOUS les domaines, puisque tous arrivent des mêmes expéditeurs.
REPORTER_SAMPLES = (
    "noreply-dmarc-support@google.com",
    "dmarcreport@microsoft.com",
    "dmarcreports@enterprise.protection.outlook.com",
    "postmaster@dmarc.yahoo.com",
    "dmarc@yahoo-inc.com",
    "dmarc_agg@auth.returnpath.net",
    "noreply@dmarc.zoho.com",
    "dmarc-noreply@linkedin.com",
)


class RuleError(ValueError):
    """Règle refusée : elle attribuerait des rapports au mauvais tenant."""


def validate(rule_type: str, pattern: str) -> str:
    """Renvoie le motif nettoyé, ou lève RuleError."""
    if rule_type not in RULE_TYPES:
        raise RuleError(f"Type de règle inconnu : {rule_type}")

    pattern = pattern.strip()
    if not pattern:
        raise RuleError("Le motif est vide")

    if rule_type == "sender":
        # La cascade évalue `sender` EN PREMIER et le juge certain (confiance 1.0) :
        # une règle sender qui matche les expéditeurs de rapports court-circuite tout
        # le reste et rafle les rapports de tous les clients.
        low = pattern.lower()
        captured = [s for s in REPORTER_SAMPLES if low in s]
        if captured:
            raise RuleError(
                f"Ce motif capte l'expéditeur « {captured[0]} », qui envoie les rapports "
                "DMARC de TOUS les domaines. Cette règle attribuerait donc les rapports "
                "de tous vos clients à ce seul domaine. Utilisez une règle sur le sujet."
            )

    if rule_type == "subject_regex":
        try:
            re.compile(pattern)
        except re.error as exc:
            raise RuleError(f"Expression régulière invalide : {exc}") from exc

    if rule_type in ("keyword", "alias") and len(pattern) < 3:
        # Un motif d'un ou deux caractères matche à peu près n'importe quel sujet.
        raise RuleError("Motif trop court : il matcherait presque tous les sujets")

    return pattern
