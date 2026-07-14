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

Un troisième cas mérite le même traitement que le réarmement, et pour la même raison :
**un changement de sévérité EST un changement de condition**, malgré une `dedup_key`
identique. Exemple réel : un domaine MTA-STS `testing` lève un `warning` sur un échec TLS
(les expéditeurs signalent, rien n'est bloqué) ; l'exploitant passe le domaine en
`enforce` — le MÊME échec devient `critical` (du courrier est désormais REFUSÉ). Si on se
contentait de laisser l'alerte ouverte en `warning`, l'aggravation ne notifierait
personne : on tairait précisément la classe de panne que cet outil existe pour combattre.

On ferme donc l'alerte existante et on en ouvre une neuve avec la nouvelle sévérité —
exactement comme pour une condition qui disparaît puis revient — plutôt que de modifier
la sévérité en place, pour trois raisons :
 - fidélité au modèle (« une alerte est un ÉTAT ») : ce sont deux états distincts, chacun
   avec sa propre date d'ouverture ;
 - ça NOTIFIE l'aggravation (la tâche qui consomme `ReconcileResult` notifie à l'ouverture
   et à la fermeture) — modifier en place ne notifierait rien ;
 - ça préserve l'historique : on peut relire quand l'échec est devenu critique.

Ce traitement est symétrique : une aggravation (`warning` → `critical`) et une
amélioration (`critical` → `warning`) suivent la même règle, sans cas particulier — la
sévérité fait partie de la condition, tout simplement.

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
        existante = ouvertes.get(cle)
        if existante is not None and existante.severity == cond.severity:
            continue                       # même condition, déjà ouverte : on ne renotifie pas

        if existante is not None:
            # Sévérité différente malgré la même dedup_key : ce N'EST PAS la même
            # condition (voir docstring du module). On ferme l'ancienne...
            existante.closed_at = now
            res.closed.append(existante)
            # ...et on force cette fermeture à être VISIBLE en base avant d'insérer la
            # neuve : l'index unique partiel `ux_alert_ouverte` (migration 0007) interdit
            # deux alertes OUVERTES sur (tenant_id, kind, dedup_key). L'ordre dans lequel
            # on ajoute les objets à la session ne détermine PAS l'ordre des requêtes
            # SQL envoyées par un flush unique — SQLAlchemy regroupe par type
            # d'opération, et rien ne garantit que l'UPDATE parte avant l'INSERT s'ils
            # sont flushés ensemble. Un flush() explicite ici, isolé, garantit que
            # l'UPDATE de fermeture part AVANT que l'INSERT de la neuve n'existe même
            # dans la session : deux flush() séparés s'exécutent dans l'ordre du
            # programme, sans ambiguïté possible. Prouvé par
            # test_changement_de_severite_ne_viole_pas_lindex_partiel.
            db.flush()

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
