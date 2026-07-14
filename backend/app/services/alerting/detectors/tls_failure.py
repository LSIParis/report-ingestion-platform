"""Des échecs de chiffrement signalés par les rapports TLS.

Le seul signal BRUYANT des trois : il se voyait déjà dans le tableau de bord. Il est ici
pour deux raisons — parce qu'il mérite une alerte, et parce qu'un cadre validé par un seul
type de condition n'est pas un cadre.

**La sévérité dépend du mode MTA-STS, et c'est tout l'intérêt.** Les MÊMES données ne
disent pas la même chose :
 - en `testing`, les expéditeurs SIGNALENT les échecs sans rien bloquer → un avertissement ;
 - en `enforce`, ils REFUSENT de livrer → **du courrier est en train d'être perdu,
   maintenant**. C'est une urgence.
"""
from __future__ import annotations

from app.services.alerting.base import CRITICAL, WARNING, Condition, register_detector
from app.services.tls_posture import posture


@register_detector("tls_failure")
def detect(db, tenant) -> list[Condition]:
    if tenant.status != "active":
        return []

    p = posture(db, days=30)      # aucun filtre tenant_id : la RLS scope la session
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
                     "mta_sts_mode": tenant.mta_sts_mode},
        ))
    return conditions
