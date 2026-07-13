"""Crée un compte et le rattache à des tenants.

    USER_PASSWORD=... python -m scripts.add_user <email> <role> [domaine ...]

    role : platform_admin  — voit tout (bypass), y compris la quarantaine
           tenant_viewer   — ne voit QUE les tenants rattachés

Sans USER_PASSWORD, un mot de passe fort est généré et affiché une seule fois.
Idempotent sur l'e-mail : réexécuter met à jour le mot de passe et les rattachements.
"""
import os
import secrets
import sys

from sqlalchemy import select

from app.auth.passwords import hash_password
from app.db.models import AppUser, Tenant, UserTenant
from app.db.session import get_session

ROLES = ("platform_admin", "tenant_viewer")


def run(email: str, role: str, domains: list[str]) -> None:
    if role not in ROLES:
        sys.exit(f"role invalide : {role} (attendu : {' | '.join(ROLES)})")
    if role == "tenant_viewer" and not domains:
        sys.exit("un tenant_viewer doit être rattaché à au moins un domaine")

    password = os.environ.get("USER_PASSWORD") or secrets.token_urlsafe(18)
    generated = "USER_PASSWORD" not in os.environ

    with get_session() as db:
        user = db.execute(select(AppUser).filter_by(email=email)).scalar_one_or_none()
        if user:
            user.role = role
            user.password_hash = hash_password(password)
            print(f"compte mis à jour : {email}")
        else:
            user = AppUser(email=email, role=role, password_hash=hash_password(password))
            db.add(user)
            db.flush()
            print(f"compte créé : {email} ({role})")

        for domain in domains:
            tenant = db.execute(
                select(Tenant).filter_by(domain=domain.lower())).scalar_one_or_none()
            if not tenant:
                sys.exit(f"tenant inconnu : {domain} — créer d'abord avec add_tenant")
            link = db.execute(select(UserTenant).filter_by(
                user_id=user.id, tenant_id=tenant.id)).scalar_one_or_none()
            if not link:
                db.add(UserTenant(user_id=user.id, tenant_id=tenant.id))
            print(f"  rattaché à {domain}")
        db.commit()

    if generated:
        print(f"\n  mot de passe (affiché une seule fois) : {password}\n")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    run(sys.argv[1], sys.argv[2], sys.argv[3:])
