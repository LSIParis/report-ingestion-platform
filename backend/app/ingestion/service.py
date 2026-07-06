from __future__ import annotations

import email
import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import Message
from email.utils import parsedate_to_datetime

import structlog

from app.db.models import Email
from app.db.session import get_session
from app.storage import ObjectStore

log = structlog.get_logger()


@dataclass(frozen=True)
class IngestSource:
    kind: str      # 'imap' | 'ses' | 'webhook'
    detail: str


@dataclass(frozen=True)
class IngestResult:
    email_id: str | None
    status: str    # 'enqueued' | 'duplicate' | 'invalid'


class IngestionService:
    """Point d'entrée UNIQUE de l'ingestion, quel que soit le transport.
    Idempotent : rejouer le même .eml ne crée pas de doublon."""

    def __init__(self, store: ObjectStore):
        self._store = store

    def ingest(self, raw_eml: bytes, source: IngestSource) -> IngestResult:
        msg: Message = email.message_from_bytes(raw_eml)

        message_id = self._extract_message_id(msg, raw_eml)
        from_address = self._header(msg, "From")
        subject = self._header(msg, "Subject")
        received_at = self._received_at(msg)

        if not message_id or not from_address:
            log.warning("ingest.invalid", source=source.kind,
                        has_msgid=bool(message_id), has_from=bool(from_address))
            return IngestResult(email_id=None, status="invalid")

        logger = log.bind(message_id=message_id, source=source.kind)

        with get_session() as session:
            existing = session.query(Email.id).filter_by(message_id=message_id).first()
            if existing:
                logger.info("ingest.duplicate", email_id=str(existing.id))
                return IngestResult(email_id=str(existing.id), status="duplicate")

            raw_key = self._raw_key(received_at, message_id)
            self._store.put(raw_key, raw_eml, content_type="message/rfc822")

            row = Email(
                tenant_id=None,
                message_id=message_id,
                from_address=from_address,
                subject=subject or "(no subject)",
                received_at=received_at,
                raw_object_key=raw_key,
                status="received",
                resolved_by=None,
            )
            session.add(row)
            session.flush()
            email_id = str(row.id)
            session.commit()

        # Enqueue APRÈS commit
        from app.workers.tasks import process_email
        process_email.delay(email_id)
        logger.info("ingest.enqueued", email_id=email_id, raw_key=raw_key)
        return IngestResult(email_id=email_id, status="enqueued")

    @staticmethod
    def _header(msg: Message, name: str) -> str | None:
        val = msg.get(name)
        return str(val).strip() if val else None

    @classmethod
    def _extract_message_id(cls, msg: Message, raw: bytes) -> str | None:
        mid = cls._header(msg, "Message-ID")
        if mid:
            return mid.strip("<>").strip()
        return "sha256:" + hashlib.sha256(raw).hexdigest()

    @staticmethod
    def _received_at(msg: Message):
        raw_date = msg.get("Date")
        if raw_date:
            try:
                return parsedate_to_datetime(raw_date)
            except (TypeError, ValueError):
                pass
        return datetime.now(timezone.utc)

    @staticmethod
    def _raw_key(received_at, message_id: str) -> str:
        digest = hashlib.sha256(message_id.encode()).hexdigest()[:16]
        return f"raw/{received_at:%Y/%m/%d}/{digest}.eml"
