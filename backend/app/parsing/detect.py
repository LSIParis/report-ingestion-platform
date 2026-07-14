"""Quel format est cette pièce jointe ? Le CONTENU décide, jamais le nom.

Le nom du fichier vient de l'expéditeur — un tiers non authentifié. Le `dmarc_adapter`
l'écrivait déjà noir sur blanc pour la décompression… mais l'aiguillage en AMONT faisait
exactement l'inverse, et c'était la cause racine :

    ".gz": "dmarc_xml"

Un rapport TLS-RPT s'appelle `google.com!exemple.fr!1752!1752.json.gz`. Extension `.gz`,
donc « DMARC », donc décompression, donc du JSON offert à un parseur XML, donc
DMARC_BAD_XML. Les rapports TLS n'étaient pas ignorés : ils étaient rangés en `failed`.

Les formats tabulaires (CSV, XLSX, PDF) n'ont pas de signature exploitable ici et ne sont
pas ambigus : leur extension reste l'aiguillage. L'ambiguïté ne concernait que les
rapports normalisés — c'est là, et seulement là, qu'on regarde le contenu.
"""
from __future__ import annotations

from app.parsing.compression import decompress

# Formats non ambigus : l'extension suffit, et c'est la seule information disponible
# (un CSV n'a pas de nombre magique).
_BY_EXTENSION = {
    ".csv": "csv",
    ".xlsx": "xlsx",
    ".xls": "xlsx",
    ".pdf": "pdf",
}

# Extensions qui PEUVENT porter un rapport normalisé — DMARC ou TLS-RPT. Le contenu
# tranchera. L'absence d'extension en fait partie : un expéditeur peut très bien ne pas
# en mettre.
_MAYBE_REPORT = {".gz", ".zip", ".xml", ".json", ""}


def _ext(filename: str | None) -> str:
    if not filename:
        return ""
    dot = filename.rfind(".")
    return filename[dot:].lower() if dot >= 0 else ""


def looks_like_report(filename: str) -> bool:
    """Cette pièce jointe PRÉTEND-elle être un rapport ? Si oui, ne pas savoir la lire
    est une ANOMALIE à tracer — pas un fichier à ignorer.

    S'appuie sur le même ensemble d'extensions ambiguës que `detect_format` (DMARC ou
    TLS-RPT possibles, y compris l'absence d'extension) : un `.txt` ou un `.png` n'a
    jamais prétendu être un rapport, son illisibilité n'intéresse personne.

    Type resserre a `str` (pas `str | None`) : le seul appelant (workers/tasks.py)
    a deja fait `if not filename: continue` avant d'appeler cette fonction --
    `filename` n'est donc jamais `None` ici. Garder `None` dans le type aurait
    maintenu une branche (et un test) qu'aucun chemin de production ne peut
    exercer ; le type resserre reflete le contrat reel plutot que de s'en garder
    indefiniment."""
    return _ext(filename) in _MAYBE_REPORT


def detect_format(payload: bytes, filename: str | None) -> str | None:
    """Le format à passer au registre d'adaptateurs, ou None si rien d'exploitable."""
    ext = _ext(filename)

    known = _BY_EXTENSION.get(ext)
    if known:
        return known

    if ext not in _MAYBE_REPORT:
        return None

    try:
        content = decompress(payload)
    except ValueError:
        # Contrat TOTAL de `decompress()` (voir compression.py) : elle ne laisse fuir
        # que `DecompressionTooLarge` ou `ValueError` — archive corrompue, tronquée,
        # bombe. On ne capture QUE ça : un `Exception` nu masquerait aussi une vraie
        # régression de programmation dans `compression.py` sous l'étiquette
        # « format non exploitable », au lieu de remonter et d'être vue.
        return None

    # Premier octet significatif : BOM et blancs ne disent rien du format.
    body = content.lstrip(b"\xef\xbb\xbf").lstrip()
    if not body:
        return None
    if body[:1] == b"{":
        return "tlsrpt_json"
    if body[:1] == b"<":
        return "dmarc_xml"
    return None
