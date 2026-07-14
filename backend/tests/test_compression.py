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
    with pytest.raises(ValueError):
        decompress(buf.getvalue())


def test_bombe_gzip_est_bornee():
    # 200 Mo de zéros compressent en quelques Ko. Sans borne, on les décompresse tous.
    bombe = gzip.compress(b"\0" * (200 * 1024 * 1024))
    with pytest.raises(DecompressionTooLarge):
        decompress(bombe)
