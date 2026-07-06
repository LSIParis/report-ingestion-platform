from app.parsing.base import ParseResult, ReportAdapter
from app.parsing.registry import register


@register("body")
class EmailBodyAdapter(ReportAdapter):
    """Corps HTML/texte du mail. Squelette : à compléter en v2 selon les profils
    (tableaux HTML via pandas.read_html, ou templates regex sur le texte)."""

    format = "body"

    def parse(self, raw: bytes, profile) -> ParseResult:
        try:
            import pandas as pd  # import local : dépendance lourde
            tables = pd.read_html(raw)
            if not tables:
                return ParseResult(status="failed",
                                   errors=[{"code": "BODY_NO_TABLE",
                                            "message": "Aucun tableau détecté dans le corps",
                                            "severity": "error"}])
            df = tables[0]
            rows = df.where(pd.notna(df), None).to_dict(orient="records")
            return ParseResult(status="ok", rows=rows, metadata={"row_count": len(rows)})
        except Exception as exc:  # noqa: BLE001
            return ParseResult(status="failed",
                               errors=[{"code": "PARSE_BODY", "message": str(exc),
                                        "severity": "fatal"}])
