from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation

from app.normalization.profiles import ReportProfile
from app.parsing.base import ParseResult


class NormalizationService:
    """Applique field_mapping + validation. Tolérant : garde les lignes valides,
    collecte les erreurs par ligne → 'partial' plutôt que tout rejeter."""

    def normalize(self, parsed: ParseResult, profile: ReportProfile) -> ParseResult:
        if parsed.status == "failed":
            return parsed

        out_rows: list[dict] = []
        errors: list[dict] = list(parsed.errors)

        for idx, raw_row in enumerate(parsed.rows):
            norm, row_errors = self._normalize_row(raw_row, profile, idx)
            errors.extend(row_errors)
            if not any(e["severity"] == "error" for e in row_errors):
                out_rows.append(norm)

        status = self._status(len(out_rows), len(parsed.rows), errors)
        return ParseResult(status=status, rows=out_rows, errors=errors,
                           metadata={**parsed.metadata,
                                     "valid_rows": len(out_rows),
                                     "input_rows": len(parsed.rows)})

    def _normalize_row(self, raw: dict, profile: ReportProfile, idx: int):
        norm, errors = {}, []
        for src_col, rule in profile.field_mapping.items():
            target, typ = rule["target"], rule["type"]
            raw_val = raw.get(src_col)

            if raw_val in (None, ""):
                if rule.get("required"):
                    errors.append(self._err("MISSING_FIELD", target, idx,
                                             "champ requis vide", "error"))
                norm[target] = None
                continue
            try:
                norm[target] = self._cast(raw_val, typ, rule)
            except (ValueError, InvalidOperation) as exc:
                errors.append(self._err("TYPE_CAST", target, idx,
                                        f"{raw_val!r}: {exc}", "error"))
                norm[target] = None
        return norm, errors

    @staticmethod
    def _cast(val: str, typ: str, rule: dict):
        val = str(val).strip()
        if typ == "string":
            return val
        if typ == "date":
            return datetime.strptime(val, rule["format"]).date().isoformat()
        if typ == "decimal":
            sep = rule.get("decimal_sep", ".")
            return str(Decimal(val.replace(sep, ".").replace(" ", "")))
        if typ == "int":
            return int(val.replace(" ", ""))
        raise ValueError(f"type inconnu '{typ}'")

    @staticmethod
    def _err(code, field, idx, msg, severity):
        return {"code": code, "field": field, "row_index": idx,
                "message": msg, "severity": severity}

    @staticmethod
    def _status(valid: int, total: int, errors: list) -> str:
        if valid == 0 and total > 0:
            return "failed"
        if any(e["severity"] == "error" for e in errors):
            return "partial"
        return "ok"
