"""Le socle des détecteurs.

Un détecteur est une fonction pure : `detect(db, tenant) -> list[Condition]`. Il ne voit
que ce que la session lui laisse voir — il n'a **aucun filtre `tenant_id` applicatif**,
c'est la RLS qui le scope (CLAUDE.md).

Conséquence à ne JAMAIS oublier : lui passer une session du plan worker (`get_session()`,
qui a BYPASSRLS) lui ferait voir TOUS les tenants, et ouvrirait les alertes d'un client
sur le domaine d'un autre. Le balayage ouvre donc une session scopée par tenant.

Ajouter un détecteur = un fichier. Comme un adaptateur de parsing ou un profil.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import structlog

log = structlog.get_logger()

WARNING = "warning"
CRITICAL = "critical"


@dataclass(frozen=True)
class Condition:
    """Une condition actuellement VRAIE pour un domaine.

    `dedup_key` l'identifie à l'intérieur de son `kind` : c'est elle qui décide si une
    alerte déjà ouverte correspond à cette condition, ou s'il faut en ouvrir une nouvelle.
    Vide quand il n'y a qu'une condition possible par domaine.
    """
    kind: str
    dedup_key: str
    severity: str
    payload: dict = field(default_factory=dict)


Detector = Callable[[object, object], list[Condition]]

_DETECTORS: dict[str, Detector] = {}


def register_detector(kind: str):
    def deco(fn: Detector) -> Detector:
        _DETECTORS[kind] = fn
        return fn
    return deco


def all_conditions(db, tenant) -> list[Condition]:
    """Toutes les conditions vraies pour ce domaine, tous détecteurs confondus.

    Un détecteur qui lève ne prive pas des autres : son échec est journalisé et on
    continue. Perdre une alerte parce qu'une autre est cassée serait le comble.
    """
    out: list[Condition] = []
    for kind, detect in _DETECTORS.items():
        try:
            out += detect(db, tenant)
        except Exception:  # noqa: BLE001 — un détecteur cassé ne casse pas les autres
            log.exception("alerting.detecteur_en_echec", kind=kind,
                          tenant_id=str(getattr(tenant, "id", None)))
    return out


# Importer les détecteurs les enregistre. Placé en fin de module pour éviter l'import
# circulaire : les détecteurs importent `Condition` et `register_detector` d'ici.
import app.services.alerting.detectors  # noqa: E402, F401
