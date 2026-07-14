"""Le contenu décide, jamais le nom du fichier.

Le nom vient de l'expéditeur. S'y fier était la cause racine du bug : un rapport TLS
s'appelle `…json.gz`, l'extension `.gz` était câblée sur « dmarc_xml », et le rapport
partait à l'adaptateur DMARC pour y mourir en DMARC_BAD_XML.
"""
import gzip
import io
import zipfile

from app.parsing.detect import detect_format, looks_like_report

XML = b"<?xml version='1.0'?><feedback><report_metadata/></feedback>"
JSON = b'{"organization-name": "Google Inc.", "policies": []}'


def test_xml_nu():
    assert detect_format(XML, "rapport.xml") == "dmarc_xml"


def test_json_nu():
    assert detect_format(JSON, "rapport.json") == "tlsrpt_json"


def test_gz_contenant_du_xml():
    assert detect_format(gzip.compress(XML), "acme!exemple.fr!1!2.xml.gz") == "dmarc_xml"


def test_gz_contenant_du_json_est_un_rapport_TLS():
    """LE cas qui cassait : extension .gz, contenu JSON."""
    nom = "google.com!exemple.fr!1752!1752.json.gz"
    assert detect_format(gzip.compress(JSON), nom) == "tlsrpt_json"


def test_zip_contenant_du_xml():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("r.xml", XML)
    assert detect_format(buf.getvalue(), "r.zip") == "dmarc_xml"


def test_extension_mensongere_le_contenu_gagne():
    """Un fichier nommé .xml qui contient du JSON est un rapport TLS. Le nom ment ;
    le contenu, non."""
    assert detect_format(JSON, "rapport.xml") == "tlsrpt_json"


def test_sans_extension_du_tout():
    assert detect_format(JSON, "piece-jointe") == "tlsrpt_json"


def test_espaces_et_BOM_avant_le_premier_octet_significatif():
    assert detect_format(b"\xef\xbb\xbf\n  " + JSON, "r.json") == "tlsrpt_json"
    assert detect_format(b"\n\t " + XML, "r.xml") == "dmarc_xml"


def test_formats_tabulaires_restent_aiguilles_par_extension():
    # Un CSV n'a pas de signature : son extension est la seule information disponible.
    assert detect_format(b"col1;col2\n1;2", "rapport.csv") == "csv"
    assert detect_format(b"%PDF-1.4", "rapport.pdf") == "pdf"


def test_contenu_inexploitable_est_ignore():
    assert detect_format(b"bonjour", "notes.txt") is None
    assert detect_format(b"", "vide.gz") is None


def test_archive_corrompue_est_ignoree_sans_lever():
    # Le worker ne doit pas tomber sur une pièce jointe pourrie.
    assert detect_format(b"\x1f\x8bcasse", "r.gz") is None


def test_looks_like_report_vrai_pour_les_extensions_ambigues():
    # Ces extensions PEUVENT porter un rapport normalisé (DMARC ou TLS-RPT) : une
    # pièce jointe illisible sous l'une d'elles est une anomalie à tracer.
    assert looks_like_report("rapport.gz") is True
    assert looks_like_report("rapport.zip") is True
    assert looks_like_report("rapport.xml") is True
    assert looks_like_report("rapport.json") is True
    assert looks_like_report("piece-jointe") is True  # pas d'extension du tout
    assert looks_like_report(None) is True  # même absence d'information : ambigu


def test_looks_like_report_faux_pour_les_formats_non_ambigus_ou_hors_sujet():
    # Extension reconnue par ailleurs (tabulaire) ou hors sujet : pas un rapport
    # normalisé, une pièce illisible sous ces extensions n'est pas une anomalie.
    assert looks_like_report("notes.txt") is False
    assert looks_like_report("image.png") is False
    assert looks_like_report("rapport.csv") is False
    assert looks_like_report("rapport.pdf") is False
