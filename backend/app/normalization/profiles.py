from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.db.models import Tenant
from app.db.session import get_session

PROFILES_DIR = Path(__file__).resolve().parents[2] / "profiles"


@dataclass(frozen=True)
class ReportProfile:
    profile_id: str
    tenant_id: str
    format: str
    detection: dict
    field_mapping: dict
    validation: dict

    @classmethod
    def from_dict(cls, d: dict) -> "ReportProfile":
        return cls(d["profile_id"], d["tenant_id"], d["format"],
                   d.get("detection", {}), d["field_mapping"], d.get("validation", {}))


@lru_cache(maxsize=256)
def load_profile(profile_id: str) -> ReportProfile:
    path = PROFILES_DIR / f"{profile_id}.json"
    return ReportProfile.from_dict(json.loads(path.read_text(encoding="utf-8")))


@lru_cache(maxsize=256)
def _domain_key(tenant_id: str) -> str:
    """Résout la clé lisible du tenant (partie locale du domaine) pour nommer les profils.
    acme.com → 'acme' → profil 'acme_xlsx'."""
    with get_session() as db:
        t = db.get(Tenant, tenant_id)
        domain = t.domain if t else tenant_id
    return domain.split(".")[0]


def select_profile(tenant_id: str, fmt: str, filename: str | None = None) -> str:
    """Résout le profil applicable. MVP : convention {domaine}_{fmt}.
    À enrichir (mots-clés du filename, versionning) sans toucher aux appelants."""
    return f"{_domain_key(tenant_id)}_{fmt}"
