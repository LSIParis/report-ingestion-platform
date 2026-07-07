from __future__ import annotations

import io

import structlog

from app.config import settings

log = structlog.get_logger()


class VirusFound(Exception):
    """Une signature a été détectée dans le fichier."""

    def __init__(self, signature: str) -> None:
        self.signature = signature
        super().__init__(f"Fichier infecté : {signature}")


class AntivirusUnavailable(Exception):
    """clamd injoignable / erreur d'infra → le pipeline doit RÉESSAYER,
    surtout pas parser un fichier non scanné (fail-safe)."""


def scan(data: bytes) -> None:
    """Scanne des octets via clamd (INSTREAM).

    - No-op si l'antivirus est désactivé (`ANTIVIRUS_ENABLED=false`).
    - Lève `VirusFound` si une signature est détectée.
    - Lève `AntivirusUnavailable` si clamd ne répond pas (→ retry côté worker).

    Le client `clamd` n'est importé qu'ici pour ne rien coûter quand l'AV est off.
    """
    if not settings.antivirus_enabled:
        return

    import clamd

    cd = clamd.ClamdNetworkSocket(host=settings.clamav_host, port=settings.clamav_port, timeout=30)
    try:
        result = cd.instream(io.BytesIO(data))
    except (clamd.ConnectionError, OSError) as exc:  # socket down, DB pas chargée…
        raise AntivirusUnavailable(str(exc)) from exc

    status, signature = result.get("stream", ("OK", None))
    if status == "FOUND":
        log.warning("antivirus.found", signature=signature)
        raise VirusFound(signature or "unknown")
