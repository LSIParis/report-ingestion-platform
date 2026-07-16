"""Clés API : génération, hachage, résolution à la frontière d'auth.

La résolution lit en plan worker (BYPASSRLS), exactement comme `login` résout un user :
c'est la seule lecture cross-tenant admise avant l'établissement du contexte tenant.
"""
import hashlib
import secrets as _secrets
from dataclasses import dataclass
from datetime import datetime, timezone

from app.db.models import ApiKey
from app.db.session import get_session

_PREFIX = {"platform": "sk_plat_", "domain": "sk_dom_"}


@dataclass(frozen=True)
class ResolvedKey:
    id: str
    tenant_id: str | None
    scope: str
    prefix: str


def hash_secret(secret: str) -> str:
    """SHA-256 hex. Suffisant : le secret est un aléa 256 bits (pas un mot de passe)."""
    return hashlib.sha256(secret.encode()).hexdigest()


def generate_key(scope: str) -> tuple[str, str, str]:
    """(secret_en_clair, prefix, key_hash). Le secret n'est jamais restocké en clair."""
    secret = _PREFIX[scope] + _secrets.token_urlsafe(32)
    return secret, secret[:14], hash_secret(secret)


def resolve(secret: str) -> ResolvedKey | None:
    """None si la clé est inconnue ou révoquée. Met à jour last_used_at (best-effort)."""
    with get_session() as db:
        key = (db.query(ApiKey)
                 .filter(ApiKey.key_hash == hash_secret(secret), ApiKey.revoked_at.is_(None))
                 .first())
        if key is None:
            return None
        key.last_used_at = datetime.now(timezone.utc)
        out = ResolvedKey(id=str(key.id),
                          tenant_id=str(key.tenant_id) if key.tenant_id else None,
                          scope=key.scope, prefix=key.prefix)
        db.commit()
        return out
