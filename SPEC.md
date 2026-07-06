# Spécification — Plateforme d'ingestion de rapports par e-mail (multitenant)

> Document autoportant destiné à un agent de code (Claude / Cursor). Il contient tout le
> nécessaire pour implémenter le projet ticket par ticket. Respecter **à la lettre** la
> section « Invariants de sécurité » — ils ne sont pas négociables.

---

## 1. RÔLE

Tu es un **architecte logiciel senior + ingénieur full-stack** spécialisé en automatisation,
parsing documentaire, traitement d'e-mails et dashboards métiers. Tu produis du code
**orienté production** : modulaire, testé, traçable, maintenable. Tu ne prends aucun raccourci
sur l'isolation multitenant.

---

## 2. CONTEXTE

Une entreprise reçoit des **rapports** envoyés par **différentes marques** dans une **boîte
mail partagée unique** (`reports@lsiparis.tech`, boîte **purement machine**, MX sous contrôle
total). Chaque mail concerne un **domaine / client** mentionné dans l'objet. Le contenu utile
est soit dans le **corps** du message, soit en **pièce jointe** (CSV, XLSX, PDF, autres).

Les données doivent être **extraites, normalisées, stockées**, puis affichées dans un
**tableau de bord d'exploitation métier**. L'application est **multitenant** : chaque domaine
est un tenant ; un utilisateur rattaché au domaine X ne doit **jamais** accéder aux données du
domaine Y.

Deux axes de complexité :
- **Extensibilité** : ajouter une marque ou un format ne doit pas toucher le cœur.
- **Isolation tenant** : une fuite inter-domaine est le pire scénario ; défense en profondeur.

L'identification du tenant depuis l'objet est **peu fiable** (casse, fautes, formats variables) :
c'est un problème de parsing à part entière, avec fallback et **quarantaine** en cas de doute.

---

## 3. OBJECTIF

Livrer une application qui :
1. surveille la boîte mail partagée et récupère les mails entrants,
2. détecte le domaine/tenant à partir de l'objet (avec quarantaine si ambigu),
3. parse le corps ou les pièces jointes via des **adaptateurs par format**,
4. normalise les données via des **profils configurables par marque**,
5. persiste avec **traçabilité** (statuts, erreurs, brut conservé) et **isolation RLS**,
6. expose une **API REST** scopée par tenant,
7. affiche un **dashboard** où chaque utilisateur ne voit que son tenant.

---

## 4. CONTRAINTES

### 4.1 Fonctionnelles
- Gérer plusieurs formats : corps mail (texte/HTML), CSV, XLSX, PDF (+ extensible).
- Ajouter une marque/format = **config** (règle en base + profil JSON), pas de déploiement.
- Parsing **traçable** : journalisation + statuts de traitement à chaque étape.
- **Parsing partiel accepté** : garder les lignes valides, lister les erreurs par ligne.
- Dashboard **lisible, rapide, orienté supervision + exploitation métier**.

### 4.2 Techniques
- **Backend** : Python 3.12, FastAPI, SQLAlchemy 2.0, Alembic, Celery + Redis.
- **DB** : PostgreSQL 16 avec **Row-Level Security**.
- **Object store** : S3/MinIO (brut `.eml` + pièces jointes ; jamais dans la DB).
- **Parsing** : pandas, openpyxl, chardet, BeautifulSoup, pdfplumber (+ Tesseract pour OCR v2).
- **Front** : React 19 + TypeScript, TanStack Query/Table, Recharts, React Router, Tailwind.
- **Auth** : JWT RS256, claims `{ sub, role, tenant_ids[] }`.
- Pipeline **asynchrone à étapes**, chaque étape **idempotente**, chaque transition persistée.

### 4.3 Sécurité & multitenant — INVARIANTS NON NÉGOCIABLES
1. **Toute** table métier porte `tenant_id`.
2. RLS **activée ET forcée** (`ENABLE` + `FORCE ROW LEVEL SECURITY`) sur les tables métier.
3. L'API se connecte avec un rôle **non-propriétaire, sans BYPASSRLS** (`app_api`).
   Le worker (cross-tenant, lignes en quarantaine) utilise un rôle **BYPASSRLS** (`app_worker`).
   Les migrations utilisent le rôle **propriétaire** (`postgres`).
4. Le contexte tenant est posé par requête via **`SET LOCAL app.current_tenant`** dans une
   **transaction** (jamais `SET` simple → fuite via le pool de connexions).
5. Une valeur de tenant non initialisée doit **refuser** (0 ligne), jamais tout exposer.
6. Le tenant demandé (`X-Tenant-Id`) doit être **⊂ des `tenant_ids` du JWT signé**.
7. Un mail non résolu (`tenant_id IS NULL`, statut `needs_review`) est **invisible de tout
   client** ; seuls `platform_admin` (bypass) et le worker le voient. **On ne devine jamais**
   le tenant : le doute part en quarantaine.
8. Un **test d'isolation cross-tenant** (A tente de lire/écrire B → échec) tourne en CI et
   **bloque le merge**.

---

## 5. ARCHITECTURE (pipeline asynchrone à étapes)

```
Boîte mail partagée (IMAP dev / SES inbound prod)
   │
   ▼
1. MAIL INGESTOR  ── dédup Message-ID, .eml brut → S3, INSERT email(status=received), enqueue
   │
   ▼
2. TENANT RESOLVER ── cascade: sender → subject_regex → keyword → alias(fuzzy) → QUARANTINE
   │
   ▼
3. PARSING ORCHESTRATOR ── sélectionne adaptateur(s) selon (tenant, format), corps + N PJ
   │        ┌───────┬───────┬───────┬────────┐
   ▼        ▼       ▼       ▼       ▼        ▼
        Body      CSV     XLSX    PDF     Custom     (ADAPTER_REGISTRY, contrat commun)
   │
   ▼
4. NORMALIZER + VALIDATOR ── field_mapping (profil JSON) → schéma canonique, status ok/partial/failed
   │
   ▼
5. PERSISTENCE (SET LOCAL tenant) ── report + report_row(jsonb) + parsing_error, RLS actif
   │
   ▼
6. API (JWT → middleware → session scopée) ─────▶ 7. DASHBOARD React (isolé par tenant)

Transversal : audit_log immuable · logs structurés + correlation_id · retries + DLQ · reprocess
```

**Frontière transport** : IMAP, SES et webhook produisent tous le même événement interne
(`IngestionService.ingest(raw_eml, source)`). Le reste du pipeline ignore l'origine → migrer
IMAP → SES ne touche qu'un module.

**Machine à états e-mail** :
```
received → tenant_resolved → processing → parsed_ok | parsed_partial | failed
              ↘ needs_review (quarantaine → POST /emails/{id}/assign-tenant)
failed → dead-letter → reprocess (depuis le brut S3, sans re-recevoir le mail)
```

---

## 6. MODÈLE DE DONNÉES (PostgreSQL)

Tables : `tenant`, `app_user`, `user_tenant`, `tenant_matching_rule`, `email`, `attachment`,
`report`, `report_row`, `parsing_error`, `audit_log`.

Points clés :
- `email.tenant_id` **nullable** (NULL = quarantaine). Idempotence via `message_id UNIQUE`.
- `report_row.data` en **JSONB** (marques hétérogènes) + colonnes canoniques promues et
  indexées (`report_date`) + index GIN sur `data`.
- `tenant_matching_rule(rule_type, pattern, priority, is_active)` : règles d'identification
  **en base** (configurable, pas codées en dur).
- `audit_log` : append-only, `metadata` en JSONB.

Migrations :
- `0001_schema` : tables + extension `pgcrypto` + index.
- `0002_rls` : rôles `app_api`/`app_worker`, helpers `current_tenant_id()`/`tenant_bypass()`,
  `ENABLE`+`FORCE` RLS, policies `USING (tenant_bypass() OR tenant_id = current_tenant_id())`
  **et** `WITH CHECK (…)` (empêche l'insertion cross-tenant).

Helper SQL sûr :
```sql
CREATE FUNCTION current_tenant_id() RETURNS uuid LANGUAGE sql STABLE AS $$
  SELECT NULLIF(current_setting('app.current_tenant', true), '')::uuid
$$;   -- non défini/vide → NULL → comparaison false → deny
```

---

## 7. STRATÉGIE DE PARSING (extensible par 2 axes orthogonaux)

- **Adaptateur** = 1 par **format**. Interface commune
  `parse(raw: bytes, profile) -> ParseResult(status, rows, errors, metadata)`.
  Enregistrement via décorateur `@register("xlsx")` dans `ADAPTER_REGISTRY`.
  → Ajouter un **format** = 1 classe, zéro modif du cœur.
- **Profil** (`profiles/*.json`) = 1 par couple **(marque, format)**. Décrit `detection`
  (feuille, header, délimiteur), `field_mapping` (colonne brute → {target, type, format,
  required, decimal_sep}), `validation`.
  → Ajouter une **marque** = 1 fichier JSON.

Par format : CSV (détection encodage/délimiteur), XLSX (feuille/header via profil),
Body (HTML `read_html`/BeautifulSoup ou texte+regex), PDF (`pdfplumber`, fallback OCR Tesseract
si texte vide → v2). Normalisation **tolérante** : lignes invalides → `partial`, erreurs
collectées par ligne (code, champ, row_index).

Identification tenant — cascade fail-safe, stoppe au 1er match fiable ; fuzzy accepté seulement
au-delà d'un seuil strict (`0.88`) ; sinon `needs_review`.

---

## 8. LIVRABLES

### 8.1 Arborescence
```
report-ingestion-platform/
├── backend/
│   ├── app/
│   │   ├── main.py                 # FastAPI + TenantMiddleware + routers + /health
│   │   ├── config.py               # pydantic-settings (toutes les env vars typées)
│   │   ├── storage.py              # ObjectStore boto3 (put/get/get_default/presign_get)
│   │   ├── celery_app.py           # config Celery (acks_late, retries, time limits)
│   │   ├── db/
│   │   │   ├── models.py           # Base + toutes les tables (SQLAlchemy 2.0)
│   │   │   └── session.py          # get_session (worker/bypass) + tenant_scoped_session (SET LOCAL)
│   │   ├── auth/
│   │   │   ├── middleware.py        # JWT → TenantContext → request.state
│   │   │   ├── deps.py             # get_db (session scopée), get_tenant_ctx, require_role
│   │   │   └── login.py            # POST /auth/login (émission JWT RS256)
│   │   ├── ingestion/
│   │   │   ├── service.py          # IngestionService.ingest (dédup, S3, INSERT, enqueue)
│   │   │   ├── imap_client.py      # ImapPoller (dev)
│   │   │   └── ses_handler.py      # handler SES→S3 (prod)
│   │   ├── tenant_resolver/resolver.py   # cascade + quarantaine
│   │   ├── parsing/
│   │   │   ├── base.py             # ReportAdapter (ABC), ParseResult
│   │   │   ├── registry.py         # @register / get_adapter
│   │   │   └── adapters/           # csv_adapter, xlsx_adapter, pdf_adapter, body_adapter
│   │   ├── normalization/
│   │   │   ├── profiles.py         # ReportProfile, load_profile, select_profile
│   │   │   └── normalizer.py       # mapping + validation tolérante
│   │   ├── persistence/service.py  # PersistenceService (SET LOCAL tenant + bulk insert)
│   │   ├── services/audit.py       # audit() append-only
│   │   ├── api/                    # reports.py, emails.py, metrics.py, admin.py, schemas.py, pagination.py
│   │   └── workers/
│   │       ├── tasks.py            # process_email (orchestration + états + retries), reprocess_report
│   │       └── imap_worker.py      # entrypoint polling (dev)
│   ├── profiles/                   # *.json (profils de parsing versionnés)
│   ├── migrations/                 # env.py (rôle propriétaire) + versions 0001_schema, 0002_rls
│   ├── scripts/seed.py             # 2 tenants, 3 users, règles
│   ├── tests/test_tenant_isolation.py   # cross-tenant DOIT échouer (CI bloquante)
│   ├── alembic.ini
│   ├── pyproject.toml
│   └── Dockerfile
├── frontend/
│   ├── src/
│   │   ├── api/                    # client.ts (tenant-aware), reports.ts, emails.ts, admin.ts
│   │   ├── auth/                   # session.ts, RequireAuth.tsx
│   │   ├── components/StatusBadge.tsx
│   │   └── pages/                  # Overview, ReportsList, ReportDetail, Quarantine, Metrics, AdminRules
│   ├── package.json
│   ├── Dockerfile / Dockerfile.prod / nginx.conf
├── infra/
│   ├── docker-compose.yml          # postgres, minio, createbuckets, redis, migrate, api, worker, imap-worker, frontend
│   └── init-roles.sql              # rôles LOGIN app_api / app_worker(BYPASSRLS)
├── keys/                           # jwt_priv.pem, jwt_pub.pem (générés via openssl)
└── SPEC.md
```

### 8.2 API (toutes les routes tenant auto-scopées via get_db + RLS)
```
POST /auth/login
GET  /health
GET  /reports?status_f=&brand=&page=          GET /reports/{id}
GET  /reports/{id}/rows        GET /reports/{id}/errors        GET /reports/{id}/raw (URL signée)
POST /reports/{id}/reprocess
GET  /emails?status_f=         GET /emails/{id}
GET  /emails/queue/quarantine  (admin)        POST /emails/{id}/assign-tenant (admin)
GET  /metrics/summary          GET /metrics/timeseries         GET /metrics/by-brand
GET/POST /admin/tenants/{id}/matching-rules   (admin)          GET /admin/queue/dead-letter (admin)
```

### 8.3 Écrans front
Overview (KPI + tendance) · Liste rapports (filtres URL-synchronisés) · Détail rapport
(données + erreurs + reprocess + fichier source) · Quarantaine (assign-tenant, auto-refresh) ·
Métriques (par marque, par temps) · Admin règles (CRUD sans déploiement).

---

## 9. PLAN DE DÉVELOPPEMENT (MVP en sprints)

- **S0 Socle** : mono-repo + CI ; compose (pg/minio/redis) ; migration `0001_schema` ;
  **migration `0002_rls` + rôles** ; test isolation. *(RLS dès le départ, jamais rétrofittée.)*
- **S1 Ingestion** : ObjectStore ; IngestionService + dédup ; ImapPoller ; worker Celery.
- **S2 Résolution + parsing** : matching_rule + TenantResolver ; CsvAdapter ; XlsxAdapter ;
  ReportProfile + select_profile.
- **S3 Normalisation + persistance** : Normalizer tolérant ; PersistenceService scopé ;
  orchestration `process_email` + états ; reprocess + retries + DLQ.
- **S4 API + Front + Auth** : middleware JWT + `X-Tenant-Id` ⊂ claims ; routes ;
  front socle ; écrans Overview/Liste/Détail ; Quarantaine + assign-tenant.
- **S5 Durcissement** : antivirus PJ (ClamAV) ; audit branché partout ; observabilité ;
  **suite isolation CI bloquante**.

Chemin critique : S0-4 (RLS) → S3-2 (persistance scopée) → S4-1 (middleware) → S4-4 (parcours).
Le parsing (S2) est parallélisable.

Hors MVP : webhook SES, PDF+OCR, éditeur de profils no-code, RBAC fin/SSO, alerting,
détection auto de profil.

---

## 10. CRITÈRES D'ACCEPTATION (Definition of Done globale)

- `docker compose up --build` démarre la stack ; `migrate` applique `0001` puis `0002`.
- `python -m scripts.seed` crée 2 tenants + 3 users.
- `POST /auth/login` (user@acme.com) → JWT avec `tenant_ids=[acme]`.
- Un mail `from=reports@acme.com` déposé dans la boîte → visible dans le dashboard **acme**,
  **absent** du dashboard **globex**.
- Un mail à l'objet non reconnu → `needs_review`, visible **uniquement** en quarantaine admin.
- `tests/test_tenant_isolation.py` **passe** (lecture ET écriture cross-tenant refusées ;
  worker bypass voit tout).
- Un rapport `partial`/`failed` est **rejouable** depuis le brut S3 sans re-recevoir le mail.
- Chaque transition d'état produit une entrée `audit_log`.

---

## 11. INSTRUCTIONS POUR L'AGENT

1. Implémente **sprint par sprint**, dans l'ordre. Ne passe au suivant qu'avec les tests verts.
2. **Écris le test d'isolation cross-tenant en S0**, avant toute route API. Il est le garde-fou.
3. Respecte les **invariants §4.3** : si une décision les met en tension, choisis toujours la
   plus restrictive et signale-le.
4. Pour toute nouvelle route tenant : passe par `get_db` (session scopée) ; **n'ajoute jamais**
   de `WHERE tenant_id` applicatif (la RLS s'en charge — sauf plan admin/bypass explicite).
5. Nouveau format → nouvel adaptateur `@register`. Nouvelle marque → nouveau profil JSON.
   Ne modifie pas l'orchestrateur pour ça.
6. Conserve **toujours** le `.eml` brut et les pièces jointes en object store (re-parsing).
7. Journalise chaque étape avec un `correlation_id` = `email_id`.
8. Demande une clarification si (et seulement si) un choix change un invariant de sécurité.
