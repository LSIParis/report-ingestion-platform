import time

import structlog
from imap_tools import AND, MailBox

from app.ingestion.service import IngestionService, IngestSource

log = structlog.get_logger()

PROCESSED_FOLDER = "Processed"

# On lit TOUT ce qui traîne dans la boîte, pas seulement les messages non lus.
#
# Le drapeau \Seen ne doit surtout pas servir de critère : il est posé par n'importe
# quel client IMAP (un webmail ouvert une fois, un antivirus, un client de test). Un
# rapport marqué « lu » par un tiers disparaissait alors DÉFINITIVEMENT du pipeline —
# constaté en production : trois rapports DMARC bloqués en boîte depuis mai, jamais
# ingérés, sans la moindre erreur.
#
# Le suivi de ce qui reste à traiter, c'est le DOSSIER, pas le drapeau : un message
# ingéré est déplacé dans Processed/. Rejouer un message déjà vu est sans danger, la
# déduplication sur Message-ID le rattrape.
UNPROCESSED = AND(all=True)


class ImapPoller:
    """Polling robuste (pas IDLE). La boîte de réception est la file d'attente :
    tout ce qui s'y trouve reste à traiter, tout ce qui est traité part dans Processed/."""

    def __init__(self, service: IngestionService, *, host: str, user: str,
                 password: str, mailbox: str = "INBOX", interval_s: int = 45):
        self._service = service
        self._host, self._user, self._pass = host, user, password
        self._mailbox, self._interval = mailbox, interval_s

    def run_forever(self) -> None:
        while True:
            try:
                self._poll_once()
            except Exception:  # noqa: BLE001 — on ne meurt jamais, on retente
                log.exception("imap.poll_failed")
            time.sleep(self._interval)

    def _poll_once(self) -> None:
        with MailBox(self._host).login(self._user, self._pass, self._mailbox) as mb:
            self.process(mb)

    def process(self, mb) -> int:
        """Ingère tout le contenu de la boîte. Renvoie le nombre de messages traités."""
        self._ensure_folder(mb, PROCESSED_FOLDER)
        done = 0
        for msg in mb.fetch(UNPROCESSED, mark_seen=False, bulk=True):
            result = self._service.ingest(
                raw_eml=msg.obj.as_bytes(),
                source=IngestSource(kind="imap", detail=self._mailbox),
            )
            if result.status in ("enqueued", "duplicate"):
                mb.move(msg.uid, PROCESSED_FOLDER)
                done += 1
            else:
                # 'invalid' : on LAISSE le message en boîte plutôt que de le perdre.
                log.warning("imap.not_ingested", uid=msg.uid, status=result.status)
        return done

    @staticmethod
    def _ensure_folder(mb, name: str) -> None:
        if not mb.folder.exists(name):
            mb.folder.create(name)
