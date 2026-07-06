import time

import structlog
from imap_tools import AND, MailBox

from app.ingestion.service import IngestionService, IngestSource

log = structlog.get_logger()

PROCESSED_FOLDER = "Processed"


class ImapPoller:
    """Polling robuste (pas IDLE). Marque les mails traités en les déplaçant vers
    Processed/ → la boîte reste un buffer visible de ce qui reste à traiter."""

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
            self._ensure_folder(mb, PROCESSED_FOLDER)
            for msg in mb.fetch(AND(seen=False), mark_seen=False, bulk=True):
                result = self._service.ingest(
                    raw_eml=msg.obj.as_bytes(),
                    source=IngestSource(kind="imap", detail=self._mailbox),
                )
                if result.status in ("enqueued", "duplicate"):
                    mb.move(msg.uid, PROCESSED_FOLDER)

    @staticmethod
    def _ensure_folder(mb: MailBox, name: str) -> None:
        if not mb.folder.exists(name):
            mb.folder.create(name)
