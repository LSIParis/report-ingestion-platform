"""Ajoute un tenant (un domaine surveillé) et sa règle de résolution DMARC.

    python -m scripts.add_tenant <domaine> ["Nom lisible"]

Idempotent : rejouable sans créer de doublon.

Pourquoi une règle `subject_regex` et **jamais** `sender` : pour des rapports DMARC,
l'expéditeur est toujours le même (google.com, microsoft.com…) quel que soit le domaine
concerné. Une règle `sender` enverrait donc TOUS les rapports de TOUS les clients dans un
seul tenant. Le domaine ne se lit que dans le sujet :

    « Report domain: exemple.com Submitter: google.com Report-ID: … »

Le sujet étant contrôlé par l'expéditeur, cette résolution est ensuite recoupée avec le
domaine déclaré dans le XML (app/parsing/guards.py) : désaccord → rien n'est écrit.
"""
import re
import sys

from sqlalchemy import select

from app.db.models import Tenant, TenantMatchingRule
from app.db.session import get_session


def dmarc_subject_pattern(domain: str) -> str:
    # (?![\w.-]) : 'sterifrance.com' ne doit pas matcher 'sterifrance.com.evil.tld'
    # ni 'notsterifrance.com' (le préfixe est couvert par 'domain:\s*').
    return rf"domain:\s*{re.escape(domain)}(?![\w.-])"


def run(domain: str, name: str | None = None) -> None:
    domain = domain.strip().lower()
    with get_session() as db:
        tenant = db.execute(
            select(Tenant).filter_by(domain=domain)).scalar_one_or_none()
        if tenant:
            print(f"tenant déjà présent : {domain} ({tenant.id})")
        else:
            tenant = Tenant(domain=domain, name=name or domain)
            db.add(tenant)
            db.flush()
            print(f"tenant créé : {domain} ({tenant.id})")

        pattern = dmarc_subject_pattern(domain)
        rule = db.execute(select(TenantMatchingRule).filter_by(
            tenant_id=tenant.id, rule_type="subject_regex",
            pattern=pattern)).scalar_one_or_none()
        if rule:
            print("  règle subject_regex déjà présente")
        else:
            db.add(TenantMatchingRule(tenant_id=tenant.id, rule_type="subject_regex",
                                      pattern=pattern, priority=20, is_active=True))
            print(f"  règle subject_regex : {pattern}")
        db.commit()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    run(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
