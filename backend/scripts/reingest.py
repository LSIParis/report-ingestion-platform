"""Re-lit le .eml brut depuis l'object store, réaligne les en-têtes stockés, et rejoue.

    python -m scripts.reingest [needs_review|failed|...]     (défaut : needs_review)

À utiliser quand la LECTURE du mail a changé (et pas seulement le parsing) : par
exemple après la correction du décodage RFC 2047, où les sujets Microsoft avaient été
stockés en base64 brut et étaient donc introuvables par le résolveur de tenant.

C'est exactement ce que permet la conservation systématique du `.eml` d'origine :
on ne redemande jamais le mail à l'expéditeur, on repart de la source.
"""
import email as email_lib
import sys

from app.config import settings
from app.db.models import Email
from app.db.session import get_session
from app.ingestion.service import decoded_header
from app.storage import ObjectStore
from app.workers.tasks import reprocess_report


def run(statuses: list[str]) -> None:
    store = ObjectStore.from_settings(settings)
    changed: list[str] = []

    with get_session() as db:
        emails = db.query(Email).filter(Email.status.in_(statuses)).all()
        for em in emails:
            msg = email_lib.message_from_bytes(store.get_default(em.raw_object_key))
            subject = decoded_header(msg, "Subject") or "(no subject)"
            from_address = decoded_header(msg, "From") or em.from_address
            if subject != em.subject or from_address != em.from_address:
                em.subject, em.from_address = subject, from_address
                changed.append(str(em.id))
        db.commit()

    for email_id in changed:
        reprocess_report.delay(email_id)
    print(f"{len(changed)} e-mail(s) re-lus depuis l'object store et remis en file "
          f"(sur {len(statuses)} statut(s) : {', '.join(statuses)})")


if __name__ == "__main__":
    run(sys.argv[1:] or ["needs_review"])
