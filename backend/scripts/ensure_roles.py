"""Crée (ou réaligne) les rôles de connexion `app_api` et `app_worker`.

Pourquoi pas un script monté dans /docker-entrypoint-initdb.d ?
Sur un endpoint Portainer **agent** (hôte Docker distant), le dépôt cloné vit sur le
serveur Portainer, pas sur l'hôte cible : le bind-mount d'un fichier du dépôt y produit
un répertoire vide, et l'init silencieusement rien. On crée donc les rôles depuis le
conteneur `migrate`, seul à détenir la connexion propriétaire.

Idempotent : rejouable à chaque déploiement, et réaligne les mots de passe.

Invariant : app_api est explicitement NOBYPASSRLS — c'est ce qui garantit que l'API ne
peut pas, même par erreur de code, contourner l'isolation multitenant.
"""
import os
import sys

import psycopg2
from psycopg2 import sql

from app.config import settings

ROLES = (
    ("app_api", "APP_API_PASSWORD", False),      # API : jamais de bypass
    ("app_worker", "APP_WORKER_PASSWORD", True),  # pipeline : cross-tenant + quarantaine
)


def main() -> None:
    conn = psycopg2.connect(settings.database_url_migrate)
    conn.autocommit = True
    with conn.cursor() as cur:
        for role, env_var, bypass in ROLES:
            password = os.environ.get(env_var)
            if not password:
                sys.exit(f"{env_var} est requis (mot de passe du rôle {role})")

            cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,))
            verb = "ALTER" if cur.fetchone() else "CREATE"

            cur.execute(sql.SQL("{verb} ROLE {role} LOGIN PASSWORD {pw} {bypass}").format(
                verb=sql.SQL(verb),
                role=sql.Identifier(role),
                pw=sql.Literal(password),
                bypass=sql.SQL("BYPASSRLS" if bypass else "NOBYPASSRLS"),
            ))
            print(f"{verb} ROLE {role} (bypassrls={bypass})", flush=True)
    conn.close()


if __name__ == "__main__":
    main()
