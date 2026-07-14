"""Ouvre les alertes dont la condition est devenue vraie, ferme celles dont la condition a
disparu, et NE TOUCHE PAS à celles qui étaient déjà ouvertes.

Toute la déduplication est là — ou plutôt, elle n'est nulle part : c'est une conséquence
du modèle. On n'écrit aucune règle « ne pas renotifier », aucun compteur, aucune fenêtre
d'apaisement. Une condition déjà ouverte n'est pas rouverte, donc pas renotifiée. Point.

Deux propriétés en découlent, toutes deux voulues :

 - **Le réconciliateur est idempotent.** On peut donc le lancer aussi souvent qu'on veut —
   à chaque rapport ingéré ET au balayage quotidien — sans se demander si on va inonder
   l'exploitant. C'est ce qui rend le crochet d'ingestion gratuit.

 - **Le réarmement est gratuit.** Un échec qui disparaît puis revient ferme une alerte et
   en rouvre une NEUVE, avec sa propre date. C'est un nouvel événement, il mérite d'être
   dit. (C'est ce que permet le caractère PARTIEL de l'index unique de la migration 0007.)

Aucun filtre `tenant_id` applicatif : la session est déjà scopée par la RLS (CLAUDE.md).
Voir `base.py` pour le piège que cela implique côté worker.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.db.models import Alert
from app.services.alerting.base import all_conditions


@dataclass
class ReconcileResult:
    opened: list[Alert] = field(default_factory=list)
    closed: list[Alert] = field(default_factory=list)


def reconcile(db, tenant) -> ReconcileResult:
    courantes = {(c.kind, c.dedup_key): c for c in all_conditions(db, tenant)}

    ouvertes = {(a.kind, a.dedup_key): a
                for a in db.query(Alert).filter(Alert.closed_at.is_(None)).all()}

    now = datetime.now(timezone.utc)
    res = ReconcileResult()

    for cle, cond in courantes.items():
        if cle in ouvertes:
            continue                       # déjà ouverte : on ne renotifie pas
        alerte = Alert(tenant_id=tenant.id, kind=cond.kind, dedup_key=cond.dedup_key,
                       severity=cond.severity, payload=cond.payload, opened_at=now)
        db.add(alerte)
        res.opened.append(alerte)

    for cle, alerte in ouvertes.items():
        if cle not in courantes:
            alerte.closed_at = now
            res.closed.append(alerte)

    db.flush()
    return res
