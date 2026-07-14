"""Décompression bornée des pièces jointes.

Le contenu vient d'Internet et n'est pas authentifié : une archive de quelques kilo-octets
peut se décompresser en plusieurs giga-octets et faire tomber le worker. Les bornes ne
sont pas une optimisation, ce sont des gardes.

Ce code vivait dans `dmarc_adapter`. Le détecteur de format en a besoin **avant** de
savoir quel adaptateur appeler — il ne peut donc pas le lui demander. D'où l'extraction.
"""
from __future__ import annotations

import gzip
import io
import zipfile

# Un rapport réel pèse quelques dizaines de Ko à quelques Mo. 64 Mo décompressés est déjà
# très large : au-delà, c'est une bombe, pas un rapport.
MAX_BYTES = 64 * 1024 * 1024
_CHUNK = 1 << 20


class DecompressionTooLarge(ValueError):
    """L'archive dépasse la taille décompressée autorisée (bombe probable)."""


def decompress(raw: bytes) -> bytes:
    """gzip, zip ou contenu nu → octets. Détection par nombre magique, pas par extension
    (le nom de fichier vient de l'expéditeur, on ne lui fait pas confiance)."""
    if raw[:2] == b"\x1f\x8b":
        return _bounded_read(gzip.GzipFile(fileobj=io.BytesIO(raw)))

    if raw[:2] == b"PK":
        # Contrat de cette fonction : ne jamais laisser fuir autre chose que
        # DecompressionTooLarge, ValueError ou OSError — tous les appelants (adaptateur
        # DMARC, adaptateur TLS-RPT, détecteur de format) se fient à ce contrat pour
        # écrire un ParseResult(status="failed") plutôt que de faire tomber tout l'email.
        # Piège : zipfile.BadZipFile N'HÉRITE PAS DE ValueError (son MRO est
        # BadZipFile -> Exception -> BaseException), contre-intuitif vu son nom. Sans
        # cette traduction explicite, une archive structurellement invalide (en-tête
        # PK mais contenu corrompu) remonterait telle quelle et échapperait au
        # `except (..., ValueError, OSError)` des appelants.
        try:
            zf = zipfile.ZipFile(io.BytesIO(raw))
        except zipfile.BadZipFile as exc:
            raise ValueError(f"zip corrompu : {exc}") from exc
        with zf as z:
            # Un rapport (XML pour DMARC, JSON pour TLS-RPT) : on ne devine pas le
            # format d'une entrée d'extension inconnue, on la rejette — dans le doute,
            # on ne traite pas plutôt que de risquer de mal interpréter le contenu.
            names = [n for n in z.namelist() if n.lower().endswith((".xml", ".json"))]
            if not names:
                raise ValueError("archive zip sans fichier .xml ou .json")
            name = names[0]
            # On se fie à la taille ANNONCÉE pour rejeter tôt, puis on borne quand même
            # la lecture : un en-tête zip peut mentir.
            if z.getinfo(name).file_size > MAX_BYTES:
                raise DecompressionTooLarge(f"{name} annonce une taille excessive")
            return _bounded_read(z.open(name))

    return raw


def _bounded_read(stream) -> bytes:
    out = io.BytesIO()
    with stream as f:
        while chunk := f.read(_CHUNK):
            out.write(chunk)
            if out.tell() > MAX_BYTES:
                raise DecompressionTooLarge(f"contenu décompressé > {MAX_BYTES} octets")
    return out.getvalue()
