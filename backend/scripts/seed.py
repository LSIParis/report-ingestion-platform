"""Seed idempotent : 2 tenants, 3 users, règles. Prouve l'isolation dès le 1er login.
Usage (dans le conteneur api ou worker) : python -m scripts.seed"""
from passlib.context import CryptContext
from sqlalchemy import select

from app.db.models import AppUser, Tenant, TenantMatchingRule, UserTenant
from app.db.session import get_session

pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")


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
        acme, _ = get_or_create(db, Tenant, domain="acme.com", defaults={"name": "ACME Corp"})
        globex, _ = get_or_create(db, Tenant, domain="globex.com", defaults={"name": "Globex"})

        admin, _ = get_or_create(db, AppUser, email="admin@platform.io",
                                 defaults={"role": "platform_admin",
                                           "password_hash": pwd.hash("admin")})
        u_acme, _ = get_or_create(db, AppUser, email="user@acme.com",
                                  defaults={"role": "tenant_viewer",
                                            "password_hash": pwd.hash("acme")})
        u_globex, _ = get_or_create(db, AppUser, email="user@globex.com",
                                    defaults={"role": "tenant_viewer",
                                              "password_hash": pwd.hash("globex")})

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
