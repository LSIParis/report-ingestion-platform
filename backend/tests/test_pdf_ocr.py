"""Tests de l'extraction par motifs (voie texte/OCR du PdfAdapter).

Le rendu OCR réel d'un PDF scanné se teste en intégration (poppler + tesseract).
Ici on couvre la logique d'extraction regex, indépendante d'un PDF.
"""
from app.parsing.adapters.pdf_adapter import apply_text_patterns


def test_apply_text_patterns_extrait_les_champs():
    text = "FACTURE ACME\nDate : 07/07/2026\nTotal TTC : 1 234,56 EUR\n"
    patterns = {
        "Date": r"Date\s*:?\s*([0-9/]{8,10})",
        "Total": r"Total\s*TTC\s*:?\s*([0-9., ]+)",
    }
    row = apply_text_patterns(text, patterns)
    assert row["Date"] == "07/07/2026"
    assert row["Total"].startswith("1 234,56")


def test_apply_text_patterns_champ_absent_donne_none():
    row = apply_text_patterns("aucune donnée utile", {"Total": r"Total\s*:\s*([0-9]+)"})
    assert row["Total"] is None
