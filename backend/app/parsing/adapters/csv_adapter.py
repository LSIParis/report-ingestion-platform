import csv
import io

import chardet
import pandas as pd

from app.parsing.base import ParseResult, ReportAdapter
from app.parsing.registry import register


@register("csv")
class CsvAdapter(ReportAdapter):
    format = "csv"

    def parse(self, raw: bytes, profile) -> ParseResult:
        try:
            encoding = chardet.detect(raw)["encoding"] or "utf-8"
            sep = profile.detection.get("delimiter") or self._sniff_sep(raw, encoding)
            df = pd.read_csv(io.BytesIO(raw), sep=sep, encoding=encoding, dtype=str)
        except Exception as exc:  # noqa: BLE001
            return ParseResult(status="failed",
                               errors=[{"code": "PARSE_CSV", "message": str(exc),
                                        "severity": "fatal"}])
        rows = df.where(pd.notna(df), None).to_dict(orient="records")
        return ParseResult(status="ok", rows=rows,
                           metadata={"encoding": encoding, "delimiter": sep,
                                     "row_count": len(rows)})

    @staticmethod
    def _sniff_sep(raw: bytes, encoding: str) -> str:
        sample = raw[:4096].decode(encoding, errors="replace")
        try:
            return csv.Sniffer().sniff(sample, delimiters=";,\t|").delimiter
        except csv.Error:
            return ";"
