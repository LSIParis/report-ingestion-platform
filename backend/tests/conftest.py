"""Fixtures d'intégration. Nécessite un PostgreSQL joignable (compose) avec migrations
appliquées (0001 + 0002). Les tests d'isolation valident les policies RLS en conditions réelles.
"""
import uuid
from datetime import datetime, timezone

import pytest

from app.db.models import Email, Report, Tenant
from app.db.session import get_session


@pytest.fixture
def seed_two_tenants():
    """Crée 2 tenants + 1 email + 1 report chacun. Yield (tid_a, tid_b). Nettoie ensuite."""
    ids: dict[str, tuple[str, str, str]] = {}

    with get_session() as db:  # plan worker (bypass) pour préparer les données
        for key, domain in [("a", "tenant-a-test.com"), ("b", "tenant-b-test.com")]:
            t = Tenant(domain=domain, name=f"Test {key}")
            db.add(t)
            db.flush()

            em = Email(
                tenant_id=t.id,
                message_id=f"test-{uuid.uuid4()}",
                from_address=f"reports@{domain}",
                subject="test",
                received_at=datetime.now(timezone.utc),
                raw_object_key="raw/test.eml",
                status="parsed_ok",
            )
            db.add(em)
            db.flush()

            rep = Report(tenant_id=t.id, email_id=em.id, source_type="body", status="ok")
            db.add(rep)
            db.flush()

            ids[key] = (str(t.id), str(em.id), str(rep.id))
        db.commit()

    yield ids["a"][0], ids["b"][0]

    # Nettoyage
    with get_session() as db:
        for key in ("a", "b"):
            tid, eid, rid = ids[key]
            db.query(Report).filter_by(id=rid).delete()
            db.query(Email).filter_by(id=eid).delete()
            db.query(Tenant).filter_by(id=tid).delete()
        db.commit()
