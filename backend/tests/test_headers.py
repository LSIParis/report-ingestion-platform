"""Décodage des en-têtes MIME (RFC 2047) à l'ingestion.

Microsoft encode TOUS les sujets de ses rapports DMARC en encoded-words. Sans
décodage, le sujet stocké est du base64 : le résolveur n'y trouve pas le domaine
et 100 % des rapports Microsoft partent en quarantaine.
"""
import email as email_lib

from app.ingestion.service import decoded_header


def _msg(headers: str):
    return email_lib.message_from_string(headers + "\n\ncorps")


def test_subject_encoded_word_base64_est_decode():
    # Sujet réel d'un rapport DMARC Microsoft.
    raw = ("Subject: =?utf-8?B?W1ByZXZpZXddIFJlcG9ydCBEb21haW46IGV4ZW1wbGUuY29tIFN1"
           "Ym1pdHRlcjogZW50ZXJwcmlzZS5wcm90ZWN0aW9uLm91dGxvb2suY29t?=")
    subject = decoded_header(_msg(raw), "Subject")
    assert subject == ("[Preview] Report Domain: exemple.com "
                       "Submitter: enterprise.protection.outlook.com")
    assert "exemple.com" in subject      # le résolveur peut enfin y lire le domaine


def test_subject_ascii_reste_intact():
    m = _msg("Subject: Report domain: exemple.com Submitter: google.com")
    assert decoded_header(m, "Subject") == "Report domain: exemple.com Submitter: google.com"


def test_from_encode_est_decode():
    m = _msg("From: =?utf-8?Q?DMARC_Aggregate_Report?= <dmarcreport@microsoft.com>")
    assert decoded_header(m, "From") == "DMARC Aggregate Report <dmarcreport@microsoft.com>"


def test_header_absent_donne_none():
    assert decoded_header(_msg("Subject: x"), "Reply-To") is None
