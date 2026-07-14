"""Rapports TLS-RPT (RFC 8460) — l'instrument sans lequel MTA-STS est aveugle.

Ils disent, jour après jour, si le courrier entrant du domaine est réellement chiffré, et
qui échoue quand il ne l'est pas. C'est ce qui rend le passage de MTA-STS en `enforce`
sûr : sans eux, on durcit à l'aveugle, et un expéditeur qui n'arrive pas à valider le
certificat cesse simplement de livrer — sans alerte, sans trace de notre côté.

Le format est du JSON (RFC 8460 §4), livré compressé ou nu. Une ligne canonique = une
OBSERVATION, comme un `<record>` DMARC :

  - `kind: "summary"` — une par politique : le bilan chiffré des sessions.
  - `kind: "failure"` — une par échec détaillé : le type, le MTA émetteur, le MX visé.

**Les compteurs portent des noms différents à dessein** : `failed_sessions` dans le
résumé, `failure_sessions` dans le détail. Ce sont les MÊMES sessions, comptées deux fois
sous deux angles. S'ils partageaient un nom, un `SUM()` sur la table les additionnerait —
et la statistique la plus regardée de l'écran (« combien d'échecs ? ») serait fausse, sans
que rien ne le signale. Le schéma rend la faute impossible plutôt que de compter sur la
vigilance de celui qui écrira la requête dans six mois.
"""
from __future__ import annotations

import json

from app.parsing.base import ParseResult, ReportAdapter
from app.parsing.compression import DecompressionTooLarge, decompress
from app.parsing.registry import register


def _date(value: str | None) -> str | None:
    """« 2026-07-13T00:00:00Z » → « 2026-07-13 ». On ne garde que le jour : la fenêtre
    d'un rapport TLS-RPT est journalière."""
    if not value or len(value) < 10:
        return None
    return value[:10]


# Les lignes émises par l'adaptateur portent déjà les noms CIBLES (`report_date`,
# `period_end`) plutôt que des noms de champ source à retraduire : contrairement au
# DMARC XML, il n'y a aucune raison de faire porter à ces deux dates un nom différent
# de celui utilisé partout ailleurs (profil, tests, dashboard). Le profil les mappe
# donc en identité — target == source, comme la plupart des autres champs ici.


@register("tlsrpt_json")
class TlsRptAdapter(ReportAdapter):
    format = "tlsrpt_json"

    def parse(self, raw: bytes, profile) -> ParseResult:
        try:
            content = decompress(raw)
        except (DecompressionTooLarge, ValueError, OSError) as exc:
            return ParseResult(status="failed",
                               errors=[{"code": "TLSRPT_DECOMPRESS",
                                        "message": str(exc), "severity": "fatal"}])

        try:
            doc = json.loads(content)
            if not isinstance(doc, dict):
                raise ValueError("le document racine n'est pas un objet JSON")
        except (ValueError, UnicodeDecodeError) as exc:
            return ParseResult(status="failed",
                               errors=[{"code": "TLSRPT_BAD_JSON",
                                        "message": str(exc), "severity": "fatal"}])

        date_range = doc.get("date-range") or {}
        header = {
            "reporter": doc.get("organization-name"),
            "report_id": doc.get("report-id"),
            "report_date": _date(date_range.get("start-datetime")),
            "period_end": _date(date_range.get("end-datetime")),
        }

        policies = doc.get("policies") or []
        rows: list[dict] = []
        errors: list[dict] = []
        policy_domain: str | None = None

        for idx, entry in enumerate(policies):
            try:
                rows += self._policy_rows(entry, header)
                policy_domain = policy_domain or _policy_domain(entry)
            except Exception as exc:  # noqa: BLE001 — une politique pourrie n'invalide
                errors.append({"code": "TLSRPT_BAD_POLICY", "row_index": idx,
                               "message": str(exc), "severity": "error"})

        if not policy_domain:
            # Sans domaine de politique, impossible de vérifier à quel tenant ce rapport
            # appartient → on refuse plutôt que de deviner (invariant §6). C'est ce champ
            # que `guard_report_domain` recoupe avec le tenant résolu depuis le sujet.
            return ParseResult(
                status="failed",
                errors=[*errors, {"code": "TLSRPT_NO_POLICY_DOMAIN", "severity": "fatal",
                                  "message": "aucun policy-domain exploitable"}],
                metadata=header)

        meta = {**header, "policy_domain": policy_domain, "row_count": len(rows)}

        # Pas de garde « rows vide » ici : `policy_domain` n'est affecté qu'après un
        # appel réussi à `_policy_rows`, qui renvoie toujours au moins une ligne
        # `summary` (voir plus bas). Si `policy_domain` est non nul, `rows` l'est donc
        # forcément aussi — contrairement à l'adaptateur DMARC, où le domaine de
        # politique vient d'un nœud distinct des `<record>` et où la garde a un sens.
        return ParseResult(status="partial" if errors else "ok",
                           rows=rows, errors=errors, metadata=meta)

    @staticmethod
    def _policy_rows(entry: dict, header: dict) -> list[dict]:
        policy = entry["policy"]
        domain = policy.get("policy-domain")

        # `mx-host` est une LISTE dans la RFC (une politique peut couvrir plusieurs MX).
        mx = policy.get("mx-host") or []
        if isinstance(mx, str):
            mx = [mx]

        common = {
            **header,
            "policy_domain": domain,
            "policy_type": policy.get("policy-type"),
            "mx_host": ", ".join(mx),
        }

        summary = entry.get("summary") or {}
        rows = [{
            **common,
            "kind": "summary",
            "successful_sessions": summary["total-successful-session-count"],
            "failed_sessions": summary["total-failure-session-count"],
        }]

        for failure in entry.get("failure-details") or []:
            rows.append({
                **common,
                "kind": "failure",
                "result_type": failure.get("result-type"),
                "sending_mta_ip": failure.get("sending-mta-ip"),
                "receiving_mx_hostname": failure.get("receiving-mx-hostname"),
                "receiving_ip": failure.get("receiving-ip"),
                "failure_sessions": failure.get("failed-session-count"),
            })

        return rows


def _policy_domain(entry: dict) -> str | None:
    policy = entry.get("policy")
    return policy.get("policy-domain") if isinstance(policy, dict) else None
