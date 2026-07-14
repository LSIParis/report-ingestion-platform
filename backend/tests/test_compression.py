"""Décompression bornée. Le contenu vient d'Internet et n'est pas authentifié : une
archive de 1 Ko peut se décompresser en 10 Go et faire tomber le worker. Les bornes ne
sont pas une optimisation, ce sont des gardes.
"""
import gzip
import io
import zipfile

import pytest

from app.parsing.compression import DecompressionTooLarge, decompress


def test_contenu_nu_est_rendu_tel_quel():
    assert decompress(b"<feedback/>") == b"<feedback/>"


def test_gzip():
    raw = gzip.compress(b'{"report-id": "x"}')
    assert decompress(raw) == b'{"report-id": "x"}'


def test_zip_xml():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("rapport.xml", "<feedback/>")
    assert decompress(buf.getvalue()) == b"<feedback/>"


def test_zip_json_est_accepte():
    # L'ancien code n'acceptait QUE des .xml dans un zip : un rapport TLS zippé était
    # refusé avant même d'être lu.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("rapport.json", '{"report-id": "x"}')
    assert decompress(buf.getvalue()) == b'{"report-id": "x"}'


def test_zip_vide_est_une_erreur():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w"):
        pass
    with pytest.raises(ValueError, match="vide"):
        decompress(buf.getvalue())


def test_zip_sans_xml_ni_json_a_un_message_distinct_du_zip_vide():
    # Deux causes differentes, deux messages differents : un zip vide n'a jamais
    # contenu de fichier, tandis qu'un zip avec un .txt (par exemple) contient
    # quelque chose -- juste rien d'exploitable. Confondre les deux enverrait
    # l'exploitant chercher la mauvaise cause.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("notes.txt", "hors sujet")
    with pytest.raises(ValueError, match="sans fichier .xml ou .json"):
        decompress(buf.getvalue())


def test_message_zip_sans_xml_ni_json_est_borne():
    # Les noms de fichiers viennent d'une archive HOSTILE, pas d'un contenu qu'on
    # controle -- et ce message est ensuite persiste tel quel dans
    # `parsing_error.message`. Une archive a 50 000 entrees produirait un message de
    # plusieurs megaoctets si on les listait toutes : on borne aux 10 premieres,
    # puis "...".
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for i in range(15):
            z.writestr(f"fichier-{i}.txt", "x")
    with pytest.raises(ValueError) as exc_info:
        decompress(buf.getvalue())
    message = str(exc_info.value)
    for i in range(10):
        assert f"fichier-{i}.txt" in message
    for i in range(10, 15):
        assert f"fichier-{i}.txt" not in message
    assert "..." in message
    assert len(message) < 1000


def test_bombe_gzip_est_bornee():
    # 200 Mo de zéros compressent en quelques Ko. Sans borne, on les décompresse tous.
    bombe = gzip.compress(b"\0" * (200 * 1024 * 1024))
    with pytest.raises(DecompressionTooLarge):
        decompress(bombe)


def test_zip_corrompu_leve_une_erreur_rattrapable():
    # zipfile.BadZipFile n'hérite PAS de ValueError (son MRO est BadZipFile ->
    # Exception -> BaseException). Le contrat de decompress() est de ne jamais laisser
    # fuir autre chose que DecompressionTooLarge ou ValueError (DecompressionTooLarge
    # en est elle-même une sous-classe) : un appelant qui fait `except ValueError` ne
    # doit jamais voir passer un ZIP cassé sous une autre forme.
    entete_pk_mais_structure_invalide = b"PK\x03\x04" + b"\x00" * 40
    with pytest.raises(ValueError):
        decompress(entete_pk_mais_structure_invalide)


def test_gzip_tronque_leve_une_erreur_rattrapable():
    # Un flux gzip valide auquel on ampute la fin lève EOFError depuis la stdlib
    # (ni ValueError ni OSError) : hors contrat sans traduction explicite.
    complet = gzip.compress(b'{"report-id": "x"}' * 100)
    tronque = complet[: len(complet) - 10]
    with pytest.raises(ValueError):
        decompress(tronque)


def test_gzip_corrompu_en_cours_de_flux_leve_une_erreur_rattrapable():
    # Corrompre des octets au milieu du flux compressé (après l'en-tête) casse
    # l'inflate en cours de lecture : zlib.error, pas ValueError ni OSError.
    complet = bytearray(gzip.compress(b'{"report-id": "x"}' * 1000))
    milieu = len(complet) // 2
    for i in range(milieu, milieu + 20):
        complet[i] ^= 0xFF
    with pytest.raises(ValueError):
        decompress(bytes(complet))


def test_zip_au_crc_invalide_leve_une_erreur_rattrapable():
    # Répertoire central valide, mais les données compressées de l'entrée sont
    # corrompues : zipfile.BadZipFile (mauvais CRC-32) est levée À LA LECTURE
    # (z.open / _bounded_read), donc hors du try/except qui n'entoure que le
    # constructeur ZipFile().
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("rapport.xml", "<feedback>" + "x" * 1000 + "</feedback>")
    data = bytearray(buf.getvalue())
    # Le contenu compressé de l'entrée suit l'en-tête local + nom de fichier ;
    # on le corrompt sans toucher au répertoire central en fin d'archive.
    offset = 30 + len("rapport.xml")
    data[offset] ^= 0xFF
    data[offset + 1] ^= 0xFF
    with pytest.raises(ValueError):
        decompress(bytes(data))
