"""Ajoute un domaine surveillé et sa règle de résolution DMARC.

    python -m scripts.add_tenant <domaine> ["Nom lisible"]

Idempotent. Même chemin que l'API d'administration (app/services/tenants.py) : un
domaine créé ici est rigoureusement identique à un domaine créé depuis l'interface.
"""
import sys

from app.db.session import get_session
from app.services.tenants import dmarc_subject_pattern, ensure_tenant


def run(domain: str, name: str | None = None) -> None:
    with get_session() as db:
        tenant, created = ensure_tenant(db, domain, name)
        db.commit()
        verb = "créé" if created else "déjà présent"
        print(f"tenant {verb} : {tenant.domain} ({tenant.id})")
        print(f"  règle subject_regex : {dmarc_subject_pattern(tenant.domain)}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    run(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
