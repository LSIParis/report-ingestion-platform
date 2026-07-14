"""Des échecs de chiffrement signalés par les rapports TLS.

Le seul signal BRUYANT des trois : il se voyait déjà dans le tableau de bord. Il est ici
pour deux raisons — parce qu'il mérite une alerte, et parce qu'un cadre validé par un seul
type de condition n'est pas un cadre.

**La sévérité dépend du mode MTA-STS, et c'est tout l'intérêt.** Les MÊMES données ne
disent pas la même chose :
 - en `testing`, les expéditeurs SIGNALENT les échecs sans rien bloquer → un avertissement ;
 - en `enforce`, ils REFUSENT de livrer → **du courrier est en train d'être perdu,
   maintenant**. C'est une urgence.

**Le panneau montre une tendance, l'alerte parle du présent — deux questions
différentes, deux fenêtres différentes.** `posture(db, days=30)` sert aussi le panneau
MTA-STS de l'écran enforce (`app/api/admin.py`) : sa fenêtre de 30 jours est calibrée
pour juger « est-ce raisonnable de durcir ? », une décision qui se prend sur une
tendance et qu'on ne touche pas ici. Une ALERTE affirme autre chose : elle dit « c'est
en train de se passer ». Elle ne peut le dire honnêtement que sur un échec vu
RÉCEMMENT — d'où sa propre fenêtre, plus courte, `ALERT_TLS_WINDOW_DAYS` (7 jours par
défaut). Sans ça, un domaine devenu silencieux (voir `domain_silent` : les rapports ont
cessé d'arriver) garderait une alerte TLS ouverte avec un libellé qui crie « maintenant »
sur une donnée vieille de trois semaines, sans que rien ne confirme que l'échec
continue — une alerte qui crie au feu sur des cendres apprend à l'exploitant à ignorer
nos alertes.
"""
from __future__ import annotations

from app.config import settings
from app.services.alerting.base import CRITICAL, WARNING, Condition, register_detector
from app.services.tls_posture import posture


@register_detector("tls_failure")
def detect(db, tenant) -> list[Condition]:
    if tenant.status != "active":
        return []

    # Fenêtre COURTE et propre à l'alerte (pas les 30 jours du panneau, voir le
    # docstring de module) : `posture()` filtre déjà ses lignes sur `report_date` par
    # rapport à cette fenêtre, donc un échec plus vieux qu'elle n'apparaît simplement
    # pas dans `p["failures"]` — l'alerte se ferme d'elle-même, ce qui est voulu.
    fenetre = settings.alert_tls_window_days
    p = posture(db, days=fenetre)  # aucun filtre tenant_id : la RLS scope la session
    severity = CRITICAL if tenant.mta_sts_mode == "enforce" else WARNING

    conditions = []
    for f in p["failures"]:
        # Le triplet (type, MTA émetteur, MX visé) est ce qui dit à l'exploitant QUOI
        # corriger : c'est donc lui, la condition — et donc la clé de déduplication.
        key = "|".join([
            f["result_type"],
            f.get("sending_mta_ip") or "",
            f.get("receiving_mx_hostname") or "",
        ])
        conditions.append(Condition(
            kind="tls_failure", dedup_key=key, severity=severity,
            payload={**f, "domain": tenant.domain,
                     "mta_sts_mode": tenant.mta_sts_mode,
                     # De quoi juger la fraîcheur SANS nous croire sur parole : la
                     # fenêtre effectivement utilisée pour retenir cet échec.
                     "window_days": fenetre},
        ))
    return conditions
