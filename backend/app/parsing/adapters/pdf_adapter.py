from __future__ import annotations

import io
import re

from app.parsing.base import ParseResult, ReportAdapter
from app.parsing.registry import register


def apply_text_patterns(text: str, patterns: dict[str, str]) -> dict:
    """Extrait une ligne {colonne_source: valeur} en appliquant un regex (1 groupe
    de capture) par colonne sur le texte. Valeur None si le motif ne matche pas.
    Utilisé pour les PDF textuels/OCR : le mapping vers le schéma canonique reste
    fait par le NormalizationService via field_mapping."""
    row: dict[str, str | None] = {}
    for col, rx in patterns.items():
        m = re.search(rx, text, re.IGNORECASE | re.MULTILINE)
        row[col] = m.group(1).strip() if m else None
    return row


@register("pdf")
class PdfAdapter(ReportAdapter):
    """PDF numériques via pdfplumber (tables). Fallback OCR (Tesseract) pour les
    PDF scannés (pas de couche texte). L'extraction structurée depuis du texte/OCR
    se fait par `detection.text_patterns` (regex par colonne) défini dans le profil."""

    format = "pdf"

    def parse(self, raw: bytes, profile) -> ParseResult:
        det = profile.detection or {}

        # 1) Tables (PDF numérique) — voie privilégiée
        try:
            rows = self._extract_tables(raw)
        except Exception as exc:  # noqa: BLE001
            return ParseResult(status="failed",
                               errors=[{"code": "PARSE_PDF", "message": str(exc),
                                        "severity": "fatal"}])
        if rows:
            return ParseResult(status="ok", rows=rows,
                               metadata={"row_count": len(rows), "mode": "tables"})

        # 2) Texte : couche texte, sinon OCR (PDF scanné)
        try:
            text, ocr = self._extract_text(raw)
        except Exception as exc:  # noqa: BLE001
            return ParseResult(status="failed",
                               errors=[{"code": "PDF_OCR_FAILED", "message": str(exc),
                                        "severity": "fatal"}])
        if not text.strip():
            return ParseResult(status="failed",
                               errors=[{"code": "PDF_EMPTY",
                                        "message": "Ni tableau ni texte exploitable",
                                        "severity": "error"}])

        # 3) Extraction par motifs si le profil en définit
        patterns = det.get("text_patterns")
        if patterns:
            row = apply_text_patterns(text, patterns)
            return ParseResult(status="ok", rows=[row],
                               metadata={"mode": "ocr" if ocr else "text", "ocr": ocr})

        # 4) Contenu capturé mais non structuré → à templater (profil), traçable
        return ParseResult(
            status="partial",
            rows=[{"raw_text": text[:10000]}],
            errors=[{"code": "PDF_NO_TEMPLATE", "severity": "warning",
                     "message": "PDF sans tableau ni text_patterns — ajouter un template au profil"}],
            metadata={"mode": "ocr" if ocr else "text", "ocr": ocr},
        )

    @staticmethod
    def _extract_tables(raw: bytes) -> list[dict]:
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
        return rows

    @staticmethod
    def _extract_text(raw: bytes) -> tuple[str, bool]:
        """Renvoie (texte, ocr_utilisé). Essaie la couche texte ; si vide,
        rasterise et passe à l'OCR Tesseract (fr+eng)."""
        import pdfplumber
        with pdfplumber.open(io.BytesIO(raw)) as pdf:
            layer = "\n".join((p.extract_text() or "") for p in pdf.pages)
        if layer.strip():
            return layer, False

        # PDF scanné → OCR
        from pdf2image import convert_from_bytes
        import pytesseract
        images = convert_from_bytes(raw, dpi=200)
        text = "\n".join(pytesseract.image_to_string(img, lang="fra+eng") for img in images)
        return text, True
