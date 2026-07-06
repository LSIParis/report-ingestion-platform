# CLAUDE.md

Règles permanentes pour ce dépôt. Chargées à chaque session. La spec détaillée est dans
`SPEC.md` — lis-la avant toute tâche d'architecture ou de nouvelle brique.

## Le projet en une phrase

Plateforme d'ingestion de rapports envoyés par différentes marques dans une boîte mail
partagée, **multitenant à isolation stricte** : parsing (corps/CSV/XLSX/PDF) → normalisation →
stockage PostgreSQL (RLS) → dashboard où chaque utilisateur ne voit **que** son domaine.

Stack : Python 3.12 · FastAPI · SQLAlchemy 2.0 · Alembic · Celery+Redis · PostgreSQL 16 (RLS) ·
S3/MinIO · React 19 + TypeScript (TanStack Query/Table, Recharts, Tailwind) · JWT RS256.

## ⛔ Invariants NON négociables (isolation multitenant)

En cas de tension sur une décision, choisis **toujours** l'option la plus restrictive et signale-le.

1. Toute table métier porte `tenant_id`. RLS `ENABLE` **et** `FORCE` sur ces tables.
2. Trois rôles DB distincts :
   - `app_api` (API) — **non-propriétaire, sans BYPASSRLS**, toujours scopé.
   - `app_worker` (pipeline) — **BYPASSRLS**, cross-tenant + lignes en quarantaine.
   - `postgres` (migrations) — propriétaire.
3. Contexte tenant posé par requête via **`SET LOCAL app.current_tenant` dans une transaction**.
   Jamais `SET` simple (fuite via le pool). Utilise `tenant_scoped_session(...)`.
4. Tenant non initialisé → **0 ligne** (deny), jamais tout exposer.
5. `X-Tenant-Id` doit être **⊂ des `tenant_ids` du JWT signé** (validé au middleware).
6. Mail non résolu (`tenant_id IS NULL`, `needs_review`) = **invisible de tout client** ;
   seuls `platform_admin` (bypass) et le worker le voient. **On ne devine jamais** le tenant :
   le doute part en quarantaine.
7. Le **test d'isolation cross-tenant** (`tests/test_tenant_isolation.py`) doit passer et
   **bloque le merge**. Écris-le/maintiens-le avant toute nouvelle route.

## Conventions de code

- **Nouvelle route tenant** : passe par `get_db` (session déjà scopée). **N'ajoute jamais** de
  `WHERE tenant_id` applicatif — la RLS le fait. (Exception : plan admin/bypass explicite.)
- **Nouveau format de parsing** : une classe `@register("<fmt>")` dans `parsing/adapters/`,
  contrat `parse(raw, profile) -> ParseResult`. Ne touche pas l'orchestrateur.
- **Nouvelle marque** : un fichier `profiles/<clé>.json` (mapping colonnes → schéma canonique).
  Aucun code, aucun déploiement.
- Conserve **toujours** le `.eml` brut + les pièces jointes dans l'object store (re-parsing).
- Parsing **tolérant** : lignes invalides → statut `partial`, erreurs collectées par ligne.
- Journalise chaque étape avec `correlation_id = email_id`. Chaque transition d'état →
  entrée `audit_log` (via `audit()`), qui ne doit jamais casser le flux métier.
- Pipeline **idempotent** : dédup sur `Message-ID` ; toute tâche est rejouable.

## Carte des modules

- Ingestion : `app/ingestion/` (`service.py` = frontière transport ; imap/ses derrière).
- Résolution : `app/tenant_resolver/resolver.py` (cascade sender→regex→keyword→alias→quarantine).
- Parsing : `app/parsing/` (base + registry + adapters).
- Normalisation : `app/normalization/` (profiles + normalizer).
- Persistance : `app/persistence/service.py` (SET LOCAL tenant + bulk insert).
- Orchestration : `app/workers/tasks.py` (`process_email`, `reprocess_report`, états, retries/DLQ).
- Sécurité : `app/auth/` (middleware, deps, login).
- API : `app/api/` (reports, emails, metrics, admin).
- Data : `app/db/` + `migrations/` (`0001_schema`, `0002_rls`).

Machine à états e-mail :
`received → tenant_resolved → processing → parsed_ok|parsed_partial|failed`
`↘ needs_review (quarantaine)` · `failed → dead-letter → reprocess (depuis S3)`.

## Commandes

```bash
# Lancer la stack (migrate applique 0001 puis 0002 RLS automatiquement)
cd infra && docker compose up --build -d

# Seed (2 tenants, 3 users)
docker compose exec api python -m scripts.seed

# Migrations
docker compose exec api alembic upgrade head
docker compose exec api alembic revision -m "message"   # nouvelle migration

# Tests (l'isolation est bloquante)
docker compose exec api pytest tests/test_tenant_isolation.py -v
docker compose exec api pytest

# Lint
docker compose exec api ruff check app
```

Clés JWT (une fois) : `openssl genrsa -out keys/jwt_priv.pem 2048 && openssl rsa -in keys/jwt_priv.pem -pubout -out keys/jwt_pub.pem`.

## Méthode de travail

- Implémente **sprint par sprint** (voir `SPEC.md` §9). Ne passe au suivant qu'avec les tests verts.
- La RLS et le test d'isolation se construisent en **Sprint 0**, jamais rétrofittés.
- Demande une clarification **seulement** si un choix modifie un invariant de sécurité ci-dessus.
