"""« Puis-je passer MTA-STS en enforce sans perdre de courrier ? »

C'est LA question que les rapports TLS existent pour trancher, et la seule qui compte
devant le sélecteur de mode : en `enforce`, un expéditeur qui n'arrive pas à valider le
certificat **cesse de livrer** — sans alerte, sans trace de notre côté.

Deux pièges, tous deux mortels, tous deux évités ici :

 - **Ne pas double-compter les échecs.** Le résumé d'une politique dit « 3 échecs » ; le
   détail décrit ces mêmes 3 échecs. Sommer les deux donnerait 6. Les champs portent donc
   des noms distincts (`failed_sessions` / `failure_sessions`) : le total vient TOUJOURS
   des lignes `summary`, le détail ne sert qu'à dire quoi et qui.

 - **Ne pas confondre silence et succès.** Un domaine dont on n'a reçu aucun rapport n'est
   pas un domaine sans échec : c'est un domaine sur lequel on ne sait rien. Y répondre
   « c'est sûr » ferait durcir à l'aveugle — exactement ce que TLS-RPT sert à éviter.
   `safe_to_enforce` exige donc des données ET aucun échec.

Le service ne connaît pas le tenant : il reçoit une session **déjà scopée**. C'est ce qui
le rend testable seul et incapable de fuiter — aucun `WHERE tenant_id` applicatif, la RLS
fait le travail (CLAUDE.md).
"""
from __future__ import annotations

from collections import Counter
from datetime import date, timedelta

from app.db.models import ReportRow

_kind = ReportRow.data["kind"].astext
_report_date = ReportRow.data["report_date"].astext


def posture(db, days: int = 30) -> dict:
    cutoff = (date.today() - timedelta(days=days)).isoformat()

    rows = (db.query(ReportRow)
              .filter(_kind.in_(("summary", "failure")))
              .filter(_report_date >= cutoff)
              .all())

    sessions_ok = 0
    sessions_failed = 0
    reporters: set[str] = set()

    # Un échec est identifié par (type, MTA émetteur, MX visé) : c'est ce triplet qui dit
    # à l'exploitant quoi corriger. Deux rapports différents décrivant le même problème
    # doivent s'additionner, pas se dupliquer.
    detail: Counter[tuple[str, str, str]] = Counter()

    for r in rows:
        d = r.data
        if d.get("reporter"):
            reporters.add(str(d["reporter"]))

        if d.get("kind") == "summary":
            sessions_ok += _int(d.get("successful_sessions"))
            sessions_failed += _int(d.get("failed_sessions"))
            continue

        key = (str(d.get("result_type") or "inconnu"),
               str(d.get("sending_mta_ip") or ""),
               str(d.get("receiving_mx_hostname") or ""))
        detail[key] += _int(d.get("failure_sessions"))

    failures = [
        {"result_type": rt, "sessions": n,
         "sending_mta_ip": ip or None, "receiving_mx_hostname": mx or None}
        for (rt, ip, mx), n in detail.most_common()
    ]

    total = sessions_ok + sessions_failed

    return {
        "days": days,
        "sessions_total": total,
        "sessions_ok": sessions_ok,
        "sessions_failed": sessions_failed,
        "failures": failures,
        # Des données ET aucun échec. Le silence n'est pas une preuve.
        "safe_to_enforce": total > 0 and sessions_failed == 0,
        "reporters": sorted(reporters),
    }


def _int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
