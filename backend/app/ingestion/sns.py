"""Vérification des messages Amazon SNS (endpoint webhook public).

L'endpoint /ingest/ses est public (appelé par AWS, pas par un utilisateur) : la
sécurité repose donc sur la vérification cryptographique de la signature SNS, pas
sur un JWT. Sans ça, n'importe qui pourrait injecter de faux e-mails.
"""
from __future__ import annotations

import base64
from urllib.parse import urlparse
from urllib.request import urlopen

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.x509 import load_pem_x509_certificate

_CERT_CACHE: dict[str, object] = {}

# Champs signés selon le type de message SNS (ordre imposé par la spec).
_KEYS_NOTIFICATION = ["Message", "MessageId", "Subject", "Timestamp", "TopicArn", "Type"]
_KEYS_SUBSCRIPTION = ["Message", "MessageId", "SubscribeURL", "Timestamp", "Token",
                      "TopicArn", "Type"]


class SnsError(Exception):
    """Message SNS invalide ou signature non vérifiée."""


def _assert_aws_host(url: str) -> None:
    host = (urlparse(url).hostname or "").lower()
    scheme = urlparse(url).scheme
    if scheme != "https" or not host.endswith(".amazonaws.com"):
        raise SnsError(f"URL non autorisée (anti-SSRF) : {url}")


def _canonical(msg: dict) -> str:
    keys = _KEYS_NOTIFICATION if msg.get("Type") == "Notification" else _KEYS_SUBSCRIPTION
    # 'Subject' n'est inclus que s'il est présent.
    return "".join(f"{k}\n{msg[k]}\n" for k in keys if k in msg)


def _public_key(cert_url: str):
    _assert_aws_host(cert_url)
    if cert_url not in _CERT_CACHE:
        pem = urlopen(cert_url, timeout=5).read()  # noqa: S310 (host validé ci-dessus)
        _CERT_CACHE[cert_url] = load_pem_x509_certificate(pem).public_key()
    return _CERT_CACHE[cert_url]


def verify(msg: dict) -> None:
    """Vérifie la signature du message SNS. Lève SnsError si invalide."""
    for field in ("Signature", "SigningCertURL", "Type"):
        if field not in msg:
            raise SnsError(f"champ SNS manquant : {field}")
    algo = hashes.SHA1() if msg.get("SignatureVersion") == "1" else hashes.SHA256()
    try:
        key = _public_key(msg["SigningCertURL"])
        key.verify(base64.b64decode(msg["Signature"]),
                   _canonical(msg).encode("utf-8"), padding.PKCS1v15(), algo)
    except SnsError:
        raise
    except Exception as exc:  # InvalidSignature, réseau, cert illisible…
        raise SnsError(f"signature SNS non vérifiée : {exc}") from exc


def confirm_subscription(subscribe_url: str) -> None:
    """Confirme l'abonnement SNS (GET du SubscribeURL). URL validée anti-SSRF."""
    _assert_aws_host(subscribe_url)
    urlopen(subscribe_url, timeout=5).read()  # noqa: S310 (host validé)
