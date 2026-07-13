"""Hachage des mots de passe (bcrypt).

On n'utilise PAS passlib : passlib 1.7.4 (dernière version, 2020) est incompatible avec
bcrypt >= 4.1. Il sonde le backend via `bcrypt.__about__` (supprimé) puis, en repli,
appelle hashpw avec un secret de plus de 72 octets — que bcrypt refuse désormais par une
ValueError. Résultat : tout hachage lève une exception, et *aucune* authentification ne
peut fonctionner. On appelle donc bcrypt directement, qui est activement maintenu.

Les empreintes restent au format bcrypt standard ($2b$…) : rien à migrer.
"""
from __future__ import annotations

import bcrypt

MAX_BYTES = 72   # limite dure de bcrypt : au-delà, le secret est silencieusement tronqué


def hash_password(password: str) -> str:
    raw = password.encode("utf-8")
    if len(raw) > MAX_BYTES:
        # Tronquer silencieusement ferait qu'un mot de passe long et un autre partageant
        # ses 72 premiers octets s'authentifieraient l'un pour l'autre. On refuse.
        raise ValueError(f"mot de passe trop long ({len(raw)} octets, max {MAX_BYTES})")
    return bcrypt.hashpw(raw, bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8")[:MAX_BYTES],
                              hashed.encode("ascii"))
    except (ValueError, TypeError):
        # Empreinte illisible ou tronquée en base → échec d'authentification, pas 500.
        return False
