import io

from app.parsing.base import ParseResult, ReportAdapter
from app.parsing.registry import register


@register("pdf")
class PdfAdapter(ReportAdapter):
    """PDF numériques via pdfplumber. Fallback OCR (Tesseract) à brancher en v2
    lorsque extract_text() est vide (PDF scanné)."""

    format = "pdf"

    def parse(self, raw: bytes, profile) -> ParseResult:
        try:
            import pdfplumber
            rows: list[dict] = []
            with pdfplumber.open(io.BytesIO(raw)) as pdf:
                for page in pdf.pages:
                    for table in page.extract_tables() or []:
                        if not table or len(table) < 2:
                            continue
                        header = [c or f"col_{i}" for i, c in enumerate(table[0])]
                        for line in table[1:]:
                            rows.append(dict(zip(header, line)))
            if not rows:
                return ParseResult(status="failed",
                                   errors=[{"code": "PDF_NO_TABLE",
                                            "message": "Aucune table extraite (PDF scanné ? → OCR v2)",
                                            "severity": "error"}])
            return ParseResult(status="ok", rows=rows, metadata={"row_count": len(rows)})
        except Exception as exc:  # noqa: BLE001
            return ParseResult(status="failed",
                               errors=[{"code": "PARSE_PDF", "message": str(exc),
                                        "severity": "fatal"}])
