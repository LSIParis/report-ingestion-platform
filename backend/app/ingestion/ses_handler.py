import structlog

from app.ingestion.service import IngestionService, IngestSource
from app.storage import ObjectStore

log = structlog.get_logger()


def handle_ses_notification(record: dict, service: IngestionService,
                            inbound_store: ObjectStore) -> None:
    """SES a déjà écrit le .eml dans S3 ; on le relit et on le passe au MÊME service.
    Aucune logique de parsing/tenant ici — juste le transport (prod)."""
    action = record["receipt"]["action"]
    raw_eml = inbound_store.get(action["bucketName"], action["objectKey"])
    result = service.ingest(
        raw_eml=raw_eml,
        source=IngestSource(kind="ses", detail=record["mail"]["messageId"]),
    )
    log.info("ses.ingested", status=result.status, email_id=result.email_id)
