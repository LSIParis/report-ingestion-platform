"""Seed idempotent : 2 tenants, 3 users, règles. Prouve l'isolation dès le 1er login.
Usage (dans le conteneur api ou worker) : python -m scripts.seed

Les tenants sont créés par `ensure_tenant`, comme le font l'API d'administration et
`scripts.add_tenant`. Ce script les fabriquait auparavant à la main : ils n'avaient donc
PAS la règle de résolution `domain: <domaine>` que reçoit tout domaine réel, et les
rapports DMARC comme TLS-RPT partaient tous en quarantaine. Un environnement de
développement qui ne se comporte pas comme la production ne prouve rien.
"""
from sqlalchemy import select

from app.auth.passwords import hash_password
from app.db.models import AppUser, TenantMatchingRule, UserTenant
from app.db.session import get_session
from app.services.tenants import ensure_tenant


def get_or_create(db, model, defaults=None, **keys):
    obj = db.execute(select(model).filter_by(**keys)).scalar_one_or_none()
    if obj:
        return obj, False
    obj = model(**keys, **(defaults or {}))
    db.add(obj)
    db.flush()
    return obj, True


def run() -> None:
    with get_session() as db:
        acme, _ = ensure_tenant(db, "acme.com", "ACME Corp")
        globex, _ = ensure_tenant(db, "globex.com", "Globex")

        admin, _ = get_or_create(db, AppUser, email="admin@platform.io",
                                 defaults={"role": "platform_admin",
                                           "password_hash": hash_password("admin")})
        u_acme, _ = get_or_create(db, AppUser, email="user@acme.com",
                                  defaults={"role": "tenant_viewer",
                                            "password_hash": hash_password("acme")})
        u_globex, _ = get_or_create(db, AppUser, email="user@globex.com",
                                    defaults={"role": "tenant_viewer",
                                              "password_hash": hash_password("globex")})

        get_or_create(db, UserTenant, user_id=u_acme.id, tenant_id=acme.id)
        get_or_create(db, UserTenant, user_id=u_globex.id, tenant_id=globex.id)

        get_or_create(db, TenantMatchingRule, tenant_id=acme.id, rule_type="sender",
                      pattern="reports@acme.com", defaults={"priority": 10})
        get_or_create(db, TenantMatchingRule, tenant_id=acme.id, rule_type="subject_regex",
                      pattern=r"^\[ACME\]", defaults={"priority": 20})
        get_or_create(db, TenantMatchingRule, tenant_id=globex.id, rule_type="sender",
                      pattern="reports@globex.com", defaults={"priority": 10})

        db.commit()
        print(f"Seed OK — acme={acme.id} globex={globex.id}")
        print("Logins: admin@platform.io/admin · user@acme.com/acme · user@globex.com/globex")


if __name__ == "__main__":
    run()
