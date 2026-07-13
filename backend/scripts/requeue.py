"""Rejoue les e-mails restés en quarantaine ou en échec.

    python -m scripts.requeue [needs_review|failed] ...    (défaut : needs_review)

Utile après avoir ajouté une règle de tenant : les mails arrivés avant la règle
sont en `needs_review` et ne se re-résolvent pas tout seuls. Le pipeline étant
idempotent (dédup Message-ID, .eml conservé dans l'object store), on peut rejouer
sans risque de doublon.
"""
import sys

from app.db.models import Email
from app.db.session import get_session
from app.workers.tasks import reprocess_report

DEFAULT_STATUSES = ["needs_review"]


def run(statuses: list[str]) -> None:
    with get_session() as db:
        ids = [str(e.id) for e in db.query(Email.id)
               .filter(Email.status.in_(statuses)).all()]

    for email_id in ids:
        reprocess_report.delay(email_id)   # purge le run précédent puis re-parse
    print(f"{len(ids)} e-mail(s) remis en file (statuts : {', '.join(statuses)})")


if __name__ == "__main__":
    run(sys.argv[1:] or DEFAULT_STATUSES)
