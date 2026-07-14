"""Rapports agrégés DMARC (RUA).

Contrairement aux rapports de marque (CSV/XLSX/PDF), les rapports DMARC suivent un
schéma XML normalisé (RFC 7489, annexe C) et arrivent quasi toujours **compressés** :
`<receiver>!<domaine>!<begin>!<end>.xml.gz` (Google, Yahoo…) ou `.zip` (Microsoft).

Le contenu vient d'Internet et n'est pas authentifié → deux gardes :
 - décompression bornée (anti zip-bomb / gzip-bomb) ;
 - parsing XML via defusedxml (anti XXE et expansion d'entités « billion laughs »).

Une ligne canonique = un `<record>` (un couple IP source / résultat d'évaluation),
enrichi des champs de niveau rapport pour que chaque ligne soit auto-portante.
"""
from __future__ import annotations

from datetime import datetime, timezone

from defusedxml.ElementTree import fromstring

from app.parsing.base import ParseResult, ReportAdapter
from app.parsing.compression import decompress
from app.parsing.registry import register


def _text(node, path: str) -> str | None:
    if node is None:
        return None
    el = node.find(path)
    return el.text.strip() if el is not None and el.text else None


def _epoch_to_date(value: str | None) -> str | None:
    """Les bornes du rapport sont des timestamps Unix UTC → date ISO."""
    if not value:
        return None
    return datetime.fromtimestamp(int(value), tz=timezone.utc).date().isoformat()


def _auth_results(record, kind: str) -> str | None:
    """Aplati auth_results/<kind>[] en 'domaine=résultat;domaine=résultat'."""
    parts = [
        f"{_text(e, 'domain') or '?'}={_text(e, 'result') or '?'}"
        for e in record.findall(f"auth_results/{kind}")
    ]
    return ";".join(parts) or None


@register("dmarc_xml")
class DmarcXmlAdapter(ReportAdapter):
    format = "dmarc_xml"

    def parse(self, raw: bytes, profile) -> ParseResult:
        try:
            xml = decompress(raw)
        except ValueError as exc:
            # `DecompressionTooLarge` est une sous-classe de `ValueError` ; le
            # contrat de `decompress()` (voir compression.py) ne laisse fuir que
            # `DecompressionTooLarge` ou `ValueError` -- `OSError` n'est jamais
            # atteignable ici, l'ajouter suggererait un cas que ce contrat exclut.
            return ParseResult(status="failed",
                               errors=[{"code": "DMARC_DECOMPRESS", "message": str(exc),
                                        "severity": "fatal"}])
        try:
            root = fromstring(xml)
        except Exception as exc:  # noqa: BLE001 — XML malformé ou hostile
            return ParseResult(status="failed",
                               errors=[{"code": "DMARC_BAD_XML", "message": str(exc),
                                        "severity": "fatal"}])

        meta = root.find("report_metadata")
        policy = root.find("policy_published")
        policy_domain = _text(policy, "domain")

        if not policy_domain:
            # Sans domaine de politique, impossible de vérifier à quel tenant ce
            # rapport appartient → on refuse plutôt que de deviner (invariant §6).
            return ParseResult(status="failed",
                               errors=[{"code": "DMARC_NO_POLICY_DOMAIN",
                                        "message": "policy_published/domain absent",
                                        "severity": "fatal"}])

        header = {
            "org_name": _text(meta, "org_name"),
            "org_email": _text(meta, "email"),
            "report_id": _text(meta, "report_id"),
            "date_begin": _epoch_to_date(_text(meta, "date_range/begin")),
            "date_end": _epoch_to_date(_text(meta, "date_range/end")),
            "policy_domain": policy_domain,
            "policy_p": _text(policy, "p"),
            "policy_sp": _text(policy, "sp"),
            "policy_pct": _text(policy, "pct"),
        }

        rows: list[dict] = []
        errors: list[dict] = []

        # Tolérant : un <record> corrompu n'invalide pas le rapport entier.
        for idx, rec in enumerate(root.findall("record")):
            try:
                dkim = _text(rec, "row/policy_evaluated/dkim")
                spf = _text(rec, "row/policy_evaluated/spf")
                rows.append({
                    **header,
                    "source_ip": _text(rec, "row/source_ip"),
                    "count": _text(rec, "row/count"),
                    "disposition": _text(rec, "row/policy_evaluated/disposition"),
                    "dkim_result": dkim,
                    "spf_result": spf,
                    # DMARC passe si DKIM **ou** SPF est aligné (RFC 7489 §6.6.2).
                    "aligned": "pass" if "pass" in (dkim, spf) else "fail",
                    "header_from": _text(rec, "identifiers/header_from"),
                    "envelope_from": _text(rec, "identifiers/envelope_from"),
                    "auth_dkim": _auth_results(rec, "dkim"),
                    "auth_spf": _auth_results(rec, "spf"),
                })
            except Exception as exc:  # noqa: BLE001
                errors.append({"code": "DMARC_BAD_RECORD", "row_index": idx,
                               "message": str(exc), "severity": "error"})

        if not rows:
            errors.append({"code": "DMARC_NO_RECORD",
                           "message": "rapport sans <record> exploitable",
                           "severity": "error"})
            return ParseResult(status="failed", errors=errors, metadata=header)

        return ParseResult(
            status="partial" if errors else "ok",
            rows=rows, errors=errors,
            metadata={**header, "row_count": len(rows)},
        )
