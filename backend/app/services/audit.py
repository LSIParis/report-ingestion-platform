import structlog

from app.db.models import AuditLog
from app.db.session import get_session

log = structlog.get_logger()


def audit(*, actor: str, action: str, tenant_id: str | None = None,
          target_id: str | None = None, target_type: str | None = None,
          metadata: dict | None = None) -> None:
    """Journal immuable (append-only). Plan système : écrit quel que soit le tenant.
    Ne doit jamais casser le flux métier."""
    try:
        with get_session() as db:
            db.add(AuditLog(actor=actor, action=action, tenant_id=tenant_id,
                            target_id=target_id, target_type=target_type,
                            metadata_=metadata))
            db.commit()
    except Exception:
        log.exception("audit.write_failed", action=action, actor=actor)
