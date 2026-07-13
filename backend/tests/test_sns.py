"""Tests de la vérification SNS (webhook SES) sans réseau."""
import pytest

from app.ingestion import sns


def test_canonical_notification_ordre_et_format():
    msg = {"Type": "Notification", "Message": "m", "MessageId": "id",
           "Timestamp": "t", "TopicArn": "arn"}
    assert sns._canonical(msg) == (
        "Message\nm\nMessageId\nid\nTimestamp\nt\nTopicArn\narn\nType\nNotification\n"
    )


def test_canonical_inclut_subject_si_present():
    msg = {"Type": "Notification", "Message": "m", "MessageId": "id",
           "Subject": "s", "Timestamp": "t", "TopicArn": "arn"}
    c = sns._canonical(msg)
    assert "Subject\ns\n" in c
    assert c.index("Subject") < c.index("Timestamp")   # ordre imposé


def test_rejette_cert_url_hors_aws():
    # garde anti-SSRF : refuser toute URL de certificat non-amazonaws
    with pytest.raises(sns.SnsError):
        sns._public_key("https://evil.example.com/cert.pem")


def test_verify_refuse_champs_manquants():
    with pytest.raises(sns.SnsError):
        sns.verify({"Type": "Notification"})   # ni Signature ni SigningCertURL
