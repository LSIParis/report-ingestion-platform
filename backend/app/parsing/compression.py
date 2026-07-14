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

# Borne la LONGUEUR du message d'erreur "archive sans .xml/.json" (voir plus bas), pas
# seulement le nombre de noms listés : un nom d'entrée zip peut à lui seul faire
# 65 535 octets (limite du champ de longueur du format).
MAX_APERCU_CHARS = 200


class DecompressionTooLarge(ValueError):
    """L'archive dépasse la taille décompressée autorisée (bombe probable)."""


def decompress(raw: bytes) -> bytes:
    """gzip, zip ou contenu nu → octets. Détection par nombre magique, pas par extension
    (le nom de fichier vient de l'expéditeur, on ne lui fait pas confiance).

    Contrat : ne laisse jamais fuir autre chose que `DecompressionTooLarge` ou
    `ValueError`. Voir `_decompress` pour la logique, et le commentaire sur
    l'enveloppe ci-dessous pour la raison du `except Exception` large."""
    try:
        return _decompress(raw)
    except DecompressionTooLarge:
        raise  # déjà au contrat, et c'est un signal de sécurité
    except Exception as exc:  # noqa: BLE001 — VOULU, voir le commentaire ci-dessus.
        # `raw` est un octet-flux hostile venu d'Internet, pas un format qu'on contrôle.
        # La liste des exceptions que les décompresseurs de la stdlib peuvent lever
        # n'est PAS énumérable de façon fiable ni stable dans le temps : EOFError (gzip
        # tronqué), zlib.error (flux gzip corrompu en cours de lecture), BadZipFile
        # (CRC-32 invalide — et il se lève à la LECTURE de l'entrée, pas seulement à
        # l'ouverture de l'archive), struct.error (en-tête zip malformé)... et demain un
        # format de plus (lzma, bz2) avec sa propre famille d'exceptions. Coder en dur
        # `except (EOFError, zlib.error, BadZipFile, struct.error)` revient à parier
        # qu'on a trouvé la liste complète — pari perdu d'avance face à du contenu
        # hostile. Le seul contrat tenable pour les appelants (adaptateur DMARC,
        # adaptateur TLS-RPT, détecteur de format) est : « ça marche, ou ça lève une
        # erreur que je sais attraper ». Sans cette traduction totale, une seule pièce
        # jointe pourrie remonte une exception inattendue jusqu'au `except Exception`
        # de `process_email`, qui marque alors TOUT L'EMAIL en échec (retries, puis
        # dead-letter) au lieu du chemin tolérant : un `ParseResult(status="failed")`
        # pour cette seule pièce jointe.
        raise ValueError(f"archive illisible : {exc}") from exc


def _decompress(raw: bytes) -> bytes:
    """Logique de décompression proprement dite — peut laisser fuir n'importe quelle
    exception de la stdlib (gzip, zlib, zipfile...) ; c'est `decompress()` qui les
    ramène toutes au contrat `DecompressionTooLarge | ValueError`."""
    if raw[:2] == b"\x1f\x8b":
        return _bounded_read(gzip.GzipFile(fileobj=io.BytesIO(raw)))

    if raw[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            entries = z.namelist()
            if not entries:
                # Distinct du cas ci-dessous : ici l'archive ne contient RIEN, pas
                # même un fichier hors-sujet. Un message différent évite à
                # l'exploitant de chercher un .xml/.json qui n'a jamais existé.
                raise ValueError("archive zip vide")
            # Un rapport (XML pour DMARC, JSON pour TLS-RPT) : on ne devine pas le
            # format d'une entrée d'extension inconnue, on la rejette — dans le doute,
            # on ne traite pas plutôt que de risquer de mal interpréter le contenu.
            names = [n for n in entries if n.lower().endswith((".xml", ".json"))]
            if not names:
                # `entries` vient d'une archive HOSTILE, pas d'un contenu qu'on
                # contrôle : une archive à 50 000 entrées produirait un message de
                # plusieurs méga-octets si on les listait toutes. Défense en
                # profondeur, pas une garantie sur ce que devient ce message
                # aujourd'hui : `detect_format` avale ce `ValueError` (il renvoie
                # `None`), et c'est `_record_unreadable` qui persiste son PROPRE
                # message dans `parsing_error.message` -- ce texte-ci n'y atterrit
                # donc pas tel quel en pratique. Mais le contrat de `decompress()`
                # (voir plus haut) est de rester utilisable par N'IMPORTE quel
                # appelant, aujourd'hui ou demain ; borner ce qu'on construit ici
                # reste la bonne discipline même si l'appelant actuel jette le
                # résultat. On borne donc à la fois le NOMBRE de noms (10, avec un
                # "..." qui dit qu'il y en a d'autres) et leur LONGUEUR totale
                # (`MAX_APERCU_CHARS`) : un seul nom d'entrée zip peut à lui seul
                # faire 65 535 octets (limite du format), donc borner uniquement
                # le nombre ne suffit pas -- dix noms de cette taille produiraient
                # quand même un message de ~640 Ko.
                apercu = ", ".join(entries[:10])
                tronque = len(entries) > 10
                if len(apercu) > MAX_APERCU_CHARS:
                    apercu = apercu[:MAX_APERCU_CHARS]
                    tronque = True
                if tronque:
                    apercu += ", ..."
                raise ValueError(
                    f"archive zip sans fichier .xml ou .json (contenu : {apercu})")
            name = names[0]
            # On se fie à la taille ANNONCÉE pour rejeter tôt, puis on borne quand même
            # la lecture : un en-tête zip peut mentir.
            if z.getinfo(name).file_size > MAX_BYTES:
                raise DecompressionTooLarge(f"{name} annonce une taille excessive")
            # zipfile.BadZipFile (CRC-32 invalide) peut être levée ICI, à la lecture,
            # bien après l'ouverture réussie de l'archive — voir decompress().
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
