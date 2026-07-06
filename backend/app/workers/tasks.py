from __future__ import annotations

import email as email_lib
from email.message import Message

import structlog

from app.celery_app import celery
from app.config import settings
from app.db.models import Attachment, Email, ParsingError, Report, ReportRow
from app.db.session import get_session
from app.normalization.normalizer import NormalizationService
from app.normalization.profiles import load_profile, select_profile
from app.parsing.base import ParseResult
from app.parsing.registry import get_adapter
from app.persistence.service import PersistenceService
from app.services.audit import audit
from app.storage import ObjectStore
from app.tenant_resolver.resolver import TenantResolverService

# Enregistre les adaptateurs dans le registre
import app.parsing.adapters  # noqa: F401

log = structlog.get_logger()
store = ObjectStore.from_settings(settings)

EXT_TO_FORMAT = {".csv": "csv", ".xlsx": "xlsx", ".xls": "xlsx", ".pdf": "pdf"}


class TransientError(Exception):
    """Erreur récupérable (S3/DB timeout…) → retry."""


@celery.task(bind=True, max_retries=4, default_retry_delay=30, acks_late=True)
def process_email(self, email_id: str) -> None:
    logger = log.bind(email_id=email_id)
    try:
        match = TenantResolverService().resolve(email_id)
        if not match.tenant_id:
            audit(actor="system", action="email.quarantined", target_id=email_id)
            return

        _set_status(email_id, "processing")
        audit(actor="system", action="email.tenant_resolved", target_id=email_id,
              tenant_id=match.tenant_id, metadata={"method": match.method})

        sources = _list_sources(email_id, match.tenant_id)
        if not sources:
            _set_status(email_id, "failed")
            audit(actor="system", action="email.failed", target_id=email_id,
                  tenant_id=match.tenant_id, metadata={"error": "no source"})
            return

        statuses = [_process_source(email_id, match.tenant_id, s) for s in sources]
        final = _aggregate(statuses)
        _set_status(email_id, final)
        audit(actor="system", action="email.processed", target_id=email_id,
              tenant_id=match.tenant_id, metadata={"result": final})

    except TransientError as exc:
        logger.warning("process.transient", error=str(exc))
        raise self.retry(exc=exc)
    except Exception as exc:  # noqa: BLE001
        logger.exception("process.fatal")
        _set_status(email_id, "failed")
        audit(actor="system", action="email.failed", target_id=email_id,
              metadata={"error": str(exc)})
        raise


@celery.task
def reprocess_report(email_id: str) -> None:
    """Reprise manuelle depuis le brut S3 — sans re-recevoir le mail. Idempotent."""
    _cleanup_previous(email_id)
    process_email.delay(email_id)


# ---------------- helpers ----------------
def _process_source(email_id: str, tenant_id: str, source: dict) -> str:
    profile_id = select_profile(tenant_id, source["fmt"], source.get("filename"))
    try:
        raw = store.get_default(source["object_key"])
        profile = load_profile(profile_id)
        parsed = get_adapter(source["fmt"]).parse(raw, profile)
        normalized = NormalizationService().normalize(parsed, profile)
    except FileNotFoundError:
        normalized = ParseResult(status="failed",
                                 errors=[{"code": "PROFILE_NOT_FOUND",
                                          "message": f"Profil '{profile_id}' introuvable",
                                          "severity": "fatal"}])
    except LookupError as exc:
        normalized = ParseResult(status="failed",
                                 errors=[{"code": "NO_ADAPTER", "message": str(exc),
                                          "severity": "fatal"}])

    PersistenceService().persist(
        tenant_id=tenant_id, email_id=email_id,
        attachment_id=source.get("attachment_id"),
        profile_id=profile_id, source_type=source["type"], result=normalized,
    )
    return normalized.status


def _list_sources(email_id: str, tenant_id: str) -> list[dict]:
    """Relit le .eml brut, extrait les pièces jointes vers S3 + rows Attachment,
    et renvoie la liste des sources parsables (une par PJ reconnue)."""
    with get_session() as db:
        em = db.get(Email, email_id)
        raw_eml = store.get_default(em.raw_object_key)

    msg: Message = email_lib.message_from_bytes(raw_eml)
    sources: list[dict] = []

    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        filename = part.get_filename()
        if not filename:
            continue
        ext = _ext(filename)
        fmt = EXT_TO_FORMAT.get(ext)
        if not fmt:
            continue  # format non géré → ignoré (traçable via metadata si besoin)

        payload = part.get_payload(decode=True) or b""
        object_key = f"attachments/{email_id}/{filename}"
        store.put(object_key, payload,
                  content_type=part.get_content_type() or "application/octet-stream")

        with get_session() as db:
            att = Attachment(tenant_id=tenant_id, email_id=email_id, filename=filename,
                             mime_type=part.get_content_type(), format=fmt,
                             object_key=object_key, size_bytes=len(payload))
            db.add(att)
            db.flush()
            attachment_id = str(att.id)
            db.commit()

        sources.append({"type": "attachment", "fmt": fmt, "object_key": object_key,
                        "attachment_id": attachment_id, "filename": filename})

    return sources


def _set_status(email_id: str, status: str) -> None:
    with get_session() as db:
        em = db.get(Email, email_id)
        if em:
            em.status = status
            db.commit()


def _aggregate(statuses: list[str]) -> str:
    if not statuses:
        return "failed"
    if all(s == "ok" for s in statuses):
        return "parsed_ok"
    if all(s == "failed" for s in statuses):
        return "failed"
    return "parsed_partial"


def _cleanup_previous(email_id: str) -> None:
    """Supprime report/rows/errors du run précédent (plan worker, cross-tenant)."""
    with get_session() as db:
        report_ids = [r.id for r in db.query(Report.id).filter_by(email_id=email_id).all()]
        if report_ids:
            db.query(ReportRow).filter(ReportRow.report_id.in_(report_ids)).delete(
                synchronize_session=False)
            db.query(ParsingError).filter(ParsingError.report_id.in_(report_ids)).delete(
                synchronize_session=False)
            db.query(Report).filter(Report.id.in_(report_ids)).delete(
                synchronize_session=False)
        db.commit()


def _ext(filename: str) -> str:
    dot = filename.rfind(".")
    return filename[dot:].lower() if dot >= 0 else ""
