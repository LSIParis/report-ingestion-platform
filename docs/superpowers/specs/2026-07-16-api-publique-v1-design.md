# API publique v1 — clés API (lecture + création de domaine)

**Date** : 2026-07-16
**Statut** : validé, prêt pour le plan d'implémentation

## Le problème

Des programmes tiers doivent pouvoir **interroger** la plateforme (domaines, rapports
agrégés, métriques, quarantaine) et **créer de nouveaux domaines** sans passer par le
dashboard ni par un compte utilisateur/mot de passe. Il faut une authentification
**machine-à-machine** qui respecte à la lettre l'isolation multitenant : une clé tierce ne
doit jamais voir les données d'un autre client que le sien.

La difficulté centrale : **créer un domaine crée un nouveau *tenant*** — c'est une action
de niveau *plateforme*, cross-tenant, alors que lire des rapports est *scopé à un tenant*.
Ces deux natures imposent **deux types de clés**.

## Principe directeur

On ne construit **aucun** chemin d'isolation neuf : une clé par-domaine produit un
`TenantContext` **identique** à celui d'un utilisateur tenant, et emprunte donc la même
machinerie RLS (`get_db` → `tenant_scoped_session`). Une clé plateforme produit un contexte
`bypass` identique à celui d'un `platform_admin`. La résolution clé→tenant se fait à la
**frontière d'auth**, exactement comme `login` résout un utilisateur : via `get_session()`
(moteur `app_worker`, BYPASSRLS), la seule lecture cross-tenant autorisée.

## Architecture

### 1. La table `api_key` (migration `0014`)

Table **d'authentification**, traitée comme `users`/`user_tenant` : **pas de RLS**. Elle
n'est jamais atteinte par une session tenant — seulement par la frontière d'auth (en
bypass) et par les routes admin de gestion. C'est le choix *le plus restrictif* : aucune
exposition possible à un client.

| Colonne | Type | Rôle |
|---|---|---|
| `id` | uuid PK | |
| `tenant_id` | uuid NULL, FK `tenant(id)` | **NULL = clé plateforme** ; renseigné = clé par-domaine |
| `scope` | text `platform`\|`domain` | explicite (redondant avec `tenant_id` NULL, mais lisible et vérifiable) |
| `prefix` | text | début lisible du secret (ex. `sk_dom_a1b2c3`), pour l'affichage/inventaire |
| `key_hash` | text UNIQUE | **SHA-256 hex** du secret complet ; le secret en clair n'est **jamais** stocké |
| `label` | text | description humaine (« ETL client X ») |
| `created_at` | timestamptz | |
| `created_by` | text | e-mail de l'admin créateur (audit) |
| `last_used_at` | timestamptz NULL | horodatage best-effort du dernier appel |
| `revoked_at` | timestamptz NULL | non-NULL = clé révoquée (refusée à l'auth) |

Contraintes : `scope='platform'` ⟺ `tenant_id IS NULL` (CHECK). Index sur `key_hash`
(lookup) et `tenant_id`.

### 2. Le secret et son hachage

- Génération : `sk_plat_` ou `sk_dom_` + 32 octets aléatoires (`secrets.token_urlsafe`).
- Le secret **entier** est haché en **SHA-256** (les jetons sont aléatoires à 256 bits :
  pas besoin de bcrypt, réservé aux secrets à faible entropie ; le hash rapide suffit et
  la comparaison se fait par égalité de hash indexé).
- `prefix` = les ~14 premiers caractères (`sk_dom_` + 6), stockés en clair pour l'inventaire.
- Le secret complet n'est retourné qu'**une seule fois**, à la création.

### 3. Résolution à la frontière — `TenantMiddleware`

Le middleware gagne une branche : si le Bearer commence par `sk_`, c'est une **clé API**.

```
token "sk_..." détecté
  → chemin ⊄ /api/v1/ ?           → 403 « clé API limitée à /api/v1 »
  → get_session() (bypass) : SELECT api_key WHERE key_hash = sha256(token) AND revoked_at IS NULL
      introuvable/révoquée         → 401 « clé API invalide »
  → best-effort : UPDATE last_used_at = now()
  → construit TenantContext :
      scope=platform → role=platform_admin, tenant_ids=(), active_tenant=None, bypass=True,
                       api_key_scope='platform'
      scope=domain   → role=tenant_viewer, tenant_ids=(tenant_id,), active_tenant=tenant_id,
                       bypass=False, api_key_scope='domain'
```

- `TenantContext` gagne un champ **`api_key_scope: str | None`** (`None` = principal JWT
  utilisateur ; `'platform'`/`'domain'` = principal clé API). C'est ce champ qui borne une
  clé à la surface `/api/v1`.
- **Le middleware reste sans DB pour les requêtes JWT** ; il ne touche la base que pour les
  requêtes portant une clé `sk_` — soit exactement la frontière d'auth, là où `login`
  interroge déjà la base.
- `/api/v1/*` est **retiré** des `PUBLIC_PATHS` : ces routes exigent un principal (clé ou
  JWT). `/docs`, `/openapi.json` restent publics (doc interactive).

### 4. La surface `/api/v1/`

Un routeur `app/api/public.py`, préfixe `/api/v1`, tag `public`. Deux dépendances de garde :

- `require_api_surface` : le principal est une clé API **ou** un JWT ; s'il s'agit d'une
  clé, `api_key_scope` doit exister (déjà garanti par le middleware). Un JWT utilisateur est
  aussi accepté sur `/api/v1` (un admin connecté peut tester l'API), avec les mêmes gardes
  de périmètre ci-dessous.
- `require_platform` : autorise si `api_key_scope=='platform'` **ou** (principal JWT et
  `role=='platform_admin'` en vue globale `bypass=True`). Sinon 403.

| Route | Garde | Comportement |
|---|---|---|
| `GET /api/v1/domains` | surface | clé domaine → **son** domaine (RLS) ; plateforme → tous. Réutilise la projection de `list_tenants`. |
| `GET /api/v1/reports` | surface | agrégats DMARC/TLS par domaine et période (`?days=`, défaut 30). Scopé RLS pour une clé domaine. |
| `GET /api/v1/metrics` | surface | séries temporelles (rapports/jour, taux de conformité). Scopé RLS pour une clé domaine. |
| `GET /api/v1/quarantine` | **platform** | rapports non attribués + métadonnées d'e-mails bruts. Cross-tenant → clé plateforme seulement. |
| `POST /api/v1/domains` | **platform** | `{domain, name?}` → 201 `{id, domain, name}`. Réutilise **exactement** la logique de `create_tenant` (409 si déjà surveillé). |

Les lectures **réutilisent les fonctions de requête existantes** (metrics/reports/admin) —
pas de duplication de logique métier ; le routeur public est une **façade** avec sa propre
sérialisation stable (contrat versionné). Réponses `application/json`, schémas Pydantic
dédiés (le contrat public ne doit pas dériver quand un schéma interne change).

### 5. Gestion des clés (routes admin JWT + UI)

Routes **admin** (principal `platform_admin`, JWT — **pas** accessibles par une clé API,
puisque hors `/api/v1`) :

- `POST /admin/api-keys` `{scope, tenant_id?, label}` → crée la clé, renvoie le **secret en
  clair une seule fois** + les métadonnées. Valide : `scope=domain` exige un `tenant_id`
  existant ; `scope=platform` interdit `tenant_id`.
- `GET /admin/api-keys` → inventaire (jamais le secret : `id, scope, tenant_id, domain,
  prefix, label, created_at, last_used_at, revoked_at`).
- `DELETE /admin/api-keys/{id}` → révocation (pose `revoked_at`, ne supprime pas la ligne :
  on garde la trace). Idempotent.

Chaque opération émet une entrée `audit()` (`api_key.created` / `api_key.revoked`).

**UI** : section « Clés API » dans **Paramètres** (page admin existante) :
- bouton « Créer une clé » → choix scope (plateforme / un domaine via sélecteur) + label ;
- à la création, le secret s'affiche **une fois** dans un encart « copiez-le maintenant » ;
- tableau des clés (prefix, scope/domaine, label, dernière utilisation, état) + bouton
  « Révoquer » (confirmation).

## Flux de données

**Création de domaine par un tiers**
```
Tiers → POST /api/v1/domains  (Authorization: Bearer sk_plat_…)
  middleware: sk_ → get_session bypass → clé plateforme valide → ctx(bypass, api_key_scope=platform)
  route: require_platform OK → logique create_tenant (bypass) → 201 {id, domain, name}
  audit(tenant.created, actor="apikey:<prefix>")
```

**Lecture des rapports d'un client par sa propre clé**
```
Tiers → GET /api/v1/reports  (Authorization: Bearer sk_dom_…)
  middleware: sk_ → clé domaine du tenant T → ctx(active_tenant=T, bypass=False, api_key_scope=domain)
  route: get_db → SET LOCAL app.current_tenant=T → requête agrégat → RLS ne renvoie que T
```

## Erreurs et dégradation

| Situation | Comportement |
|---|---|
| Bearer absent sur `/api/v1` | 401 « Bearer token manquant » (déjà géré). |
| Clé `sk_` inconnue / révoquée | 401 « clé API invalide ». |
| Clé API sur un chemin hors `/api/v1` | 403 « clé API limitée à /api/v1 ». |
| Clé domaine sur une route `platform` (POST domaine, quarantaine) | 403 (garde `require_platform`). |
| `POST /admin/api-keys` `scope=domain` sans `tenant_id` valide | 400. |
| Domaine déjà surveillé (`POST /api/v1/domains`) | 409 (repris de `create_tenant`). |
| Tenant non résolu / session non initialisée | **0 ligne** (deny by default, RLS). |

## Isolation — invariants

- Une **clé domaine** = un `TenantContext` scopé strictement identique à un utilisateur
  tenant ; **aucun** `WHERE tenant_id` applicatif dans les routes de lecture (la RLS le
  fait). Elle ne peut pas atteindre `/admin/*`, ne peut pas créer de domaine, ne voit pas
  la quarantaine.
- Une **clé plateforme** = `bypass=True`, comme `platform_admin`, mais **bornée à la surface
  `/api/v1`** par `api_key_scope` : elle ne peut pas appeler `/admin/api-keys` (créer
  d'autres clés), ni supprimer un tenant, ni gérer les utilisateurs.
- `api_key` sans RLS : justifié (table d'auth, comme `users`), signalé, et *plus*
  restrictif que l'alternative — jamais lisible par une session cliente.
- **Test d'isolation cross-tenant étendu** (`tests/test_tenant_isolation.py`, invariant #7,
  bloque le merge) : avant toute route neuve.

## Tests / vérification

- **Backend** (pytest) :
  - **Auth clé** : `sk_` valide (domaine) → contexte scopé ; `sk_` plateforme → bypass ;
    clé révoquée → 401 ; clé inconnue → 401 ; clé API sur `/admin/...` → 403.
  - **Isolation** (dans `test_tenant_isolation.py`) : clé domaine du tenant A appelant
    `GET /api/v1/reports`/`/domains` ne renvoie **que** A ; ne peut pas `POST /api/v1/domains`
    (403) ; ne voit pas `/api/v1/quarantine` (403).
  - **Endpoints** : `GET /api/v1/domains|reports|metrics` (formes de réponse, scope) ;
    `POST /api/v1/domains` (201, 409 doublon, 403 clé domaine) ; `GET /api/v1/quarantine`
    (plateforme 200, domaine 403).
  - **Gestion** : `POST/GET/DELETE /admin/api-keys` (secret rendu une fois, jamais relisté ;
    validation scope/tenant_id ; révocation idempotente ; audit émis).
  - `pytest` complet + `ruff check app scripts tests` verts ; isolation verte.
- **Frontend** : `tsc -b` + `vite build` verts ; contrôle réel : créer une clé plateforme,
  `curl` un `POST /api/v1/domains` et un `GET /api/v1/domains`, puis créer une clé domaine et
  vérifier qu'un `GET /api/v1/reports` ne renvoie que son domaine et qu'un `POST` est refusé.

## Ce qu'on ne fait pas, délibérément (v1)

- **Rate-limiting / quotas** — risque assumé pour la v1 ; mitigé par des clés **révocables**
  et une surface de lecture **bornée**. Chantier séparé (à prévoir avant large diffusion).
- **Écriture au-delà de la création** — pas de `PATCH`/`DELETE` de domaine par API (nom,
  `alert_email`, suspension, suppression restent dans l'UI admin).
- **OAuth / scopes fins par clé** — deux scopes suffisent (plateforme / domaine).
- **Webhooks sortants / pagination cursor avancée** — hors périmètre ; la pagination réutilise
  le helper existant (`app/api/pagination.py`) si nécessaire.
- **Rotation automatique des clés** — révoquer + recréer manuellement.
