import io

import pandas as pd

from app.parsing.base import ParseResult, ReportAdapter
from app.parsing.registry import register


@register("xlsx")
class XlsxAdapter(ReportAdapter):
    format = "xlsx"

    def parse(self, raw: bytes, profile) -> ParseResult:
        det = profile.detection
        try:
            df = pd.read_excel(
                io.BytesIO(raw),
                sheet_name=det.get("sheet_name", 0),
                header=det.get("header_row", 0),
                dtype=str,
                engine="openpyxl",
            )
        except Exception as exc:  # noqa: BLE001
            return ParseResult(status="failed",
                               errors=[{"code": "PARSE_XLSX", "message": str(exc),
                                        "severity": "fatal"}])
        df = df.dropna(how="all")
        rows = df.where(pd.notna(df), None).to_dict(orient="records")
        return ParseResult(status="ok", rows=rows,
                           metadata={"sheet": det.get("sheet_name"), "row_count": len(rows)})
