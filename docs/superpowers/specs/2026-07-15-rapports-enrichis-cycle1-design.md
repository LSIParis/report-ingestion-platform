# Rapports enrichis — Cycle 1 : fondation + liste + onglets

**Date** : 2026-07-15
**Statut** : validé, prêt pour le plan d'implémentation

## Le problème

La page **Rapports** est aujourd'hui minimale : un tableau à quatre colonnes (Reçu, Source,
Lignes, Statut), deux filtres (statut, marque/expéditeur), pagination. Pour savoir de quoi
parle un rapport — DMARC ou TLS ? quel émetteur ? combien de messages, combien en échec ? —
il faut l'ouvrir. Ces informations existent, mais **seulement dans les lignes** du rapport
(le JSONB `data`), pas au niveau du rapport lui-même.

Ce cycle pose la **fondation** qui rend ces informations disponibles au niveau du rapport,
et l'exploite pour enrichir la liste (colonnes) et la filtrer par type (onglets). C'est le
premier des deux cycles ; le **cycle 2** (vue détail : bandeau de synthèse, regroupement/tri
des lignes, liens d'enquête) s'appuiera sur cette même fondation et fait l'objet d'un spec
séparé.

## Architecture

Le pipeline actuel est `parse → normalize → persist` (voir `app/workers/tasks.py`). La
persistance reçoit donc des lignes **déjà normalisées** : les clés sont canoniques
(`reporter`, `message_count`, `aligned`, `kind`, `failure_sessions`). C'est le bon endroit
pour résumer le rapport.

### 1. Modèle de données — colonnes dénormalisées sur `report`

À l'ingestion, on résume le rapport et on écrit ces colonnes sur la table `report` :

| Colonne | Type | Dérivé de |
|---|---|---|
| `kind` | `text` non nul (`'dmarc'` / `'tls'`) | `result.format` : `tlsrpt_json` → `'tls'`, sinon `'dmarc'` |
| `reporter` | `text` nullable | `data["reporter"]` (canonique pour les deux types) |
| `total_units` | `int` nullable | DMARC : Σ `message_count` · TLS : Σ sessions |
| `failing_units` | `int` nullable | DMARC : Σ `message_count` où `aligned != 'pass'` · TLS : sessions en échec |
| `period_start` | `date` nullable | `date_begin` du rapport |
| `period_end` | `date` nullable | `date_end` du rapport |

**`kind` depuis le format, pas depuis les clés.** `ParseResult` porte déjà `format`
(`dmarc_xml`, `tlsrpt_json`, `csv`, `xlsx`, `pdf`, `body`). Seul `tlsrpt_json` produit du
TLS ; tout le reste est du DMARC. On dérive donc `kind = 'tls' if result.format ==
'tlsrpt_json' else 'dmarc'`. Le plan vérifiera que `normalize()` **préserve** `format` ;
à défaut, repli sur le contenu des lignes (présence de `message_count`/`aligned` → DMARC,
`kind ∈ {summary, failure}` → TLS).

**`total_units` / `failing_units` nullable — la discipline « null ≠ 0 ».** On réutilise la
règle déjà en place côté TLS (`app/services/counters.int_or_none`, `tls_posture`) : un
rapport dont la magnitude est **illisible** ne doit jamais être compté comme `0`. `null`
signifie « inconnu » et se distingue de `0` (« vrai zéro »). Afficher « 0 % d'échec » sur un
rapport dont on ne sait pas lire les compteurs serait rassurant **et faux** — c'est
précisément l'erreur que cette plateforme refuse (faux vert = danger).

**La période est stockée dès ce cycle**, bien que la liste ne l'affiche pas : le **cycle 2**
(bandeau de synthèse) en a besoin. Comme le backfill est coûteux à rejouer, la stocker
maintenant évite une seconde migration + un second backfill. Prévoyance sur un besoin déjà
décidé, pas spéculation.

### 2. Ingestion — une fonction pure `summarize()`

Un nouveau module `app/persistence/summary.py` expose :

```
summarize(kind: str, rows: list[dict]) -> ReportSummary
```

où `ReportSummary` porte `reporter`, `total_units`, `failing_units`, `period_start`,
`period_end`. La fonction est **pure et testable** (aucun accès DB), et isole la seule
logique métier du lot : comment lire le volume et l'échec selon le type.

- **DMARC** : `total = Σ message_count` ; `failing = Σ message_count où aligned != 'pass'`.
  Même définition que `app/api/metrics.py` (`aligned == 'pass'` = conforme).
- **TLS** : total et échec comptés en **sessions**, avec la discipline `int_or_none` de
  `tls_posture` (un compteur illisible → `null`, jamais `0` ; le résultat est un minorant si
  au moins un compteur est illisible).
- **Émetteur / période** : lus dans l'en-tête du rapport (identiques sur toutes les lignes) ;
  `reporter` canonique, `period_start/end` depuis `date_begin/date_end`.

`PersistenceService.persist()` appelle `summarize()` et pose les colonnes sur le `Report`
avant le `flush`. Un **retraitement** (`reprocess`) repasse par `persist` : le résumé est
donc recalculé sans traitement particulier.

### 3. Backfill des rapports existants — migration `0010`

`0010_report_summary.py` :

1. **Ajoute** les six colonnes. `kind` est ajoutée nullable, puis passée **`NOT NULL`**
   à la fin de la migration, une fois le backfill fait : tout rapport est dérivable en
   `dmarc` ou `tls`, et l'ingestion la pose toujours — un `NOT NULL` est donc sûr et évite
   une valeur manquante ambiguë en aval. Les cinq autres colonnes restent nullable
   (« inconnu » est une valeur légitime pour l'émetteur, les compteurs et la période).
2. **Backfille** les rapports existants dans la même migration, en **réutilisant
   `summarize()`** : la migration charge les lignes de chaque rapport et applique la même
   fonction. Une seule source de vérité pour les règles — aucune logique dupliquée en SQL,
   donc aucun risque de divergence entre l'ingestion et le backfill.

La migration s'exécute **automatiquement au déploiement** : le service `migrate` fait déjà
`ensure_roles && alembic upgrade head`. Aucune étape manuelle à oublier. Le backfill est
idempotent (rejouable sans dommage : il recalcule).

### 4. API

- `ReportOut` (`app/api/schemas.py`) gagne : `kind`, `reporter`, `total_units`,
  `failing_units`, `period_start`, `period_end`.
- `GET /reports` : nouveau filtre **`kind`** (`?kind=dmarc|tls`) — un simple
  `WHERE report.kind = …`, trivial et correct grâce à la dénormalisation. Les filtres
  existants (`status_f`, `brand`) sont conservés et se combinent.
- **Isolation inchangée** : tout passe par la session déjà scopée (RLS), **aucun**
  `WHERE tenant_id` applicatif (invariant CLAUDE.md). Le test d'isolation cross-tenant reste
  vert et bloquant.

### 5. Frontend — `ReportsList.tsx`

- **Onglets** DMARC / TLS / Tous en tête de page, pilotant `?kind=` dans l'URL (comme les
  filtres actuels passent par les *search params*). « Tous » = pas de paramètre `kind`.
- **Colonnes** — on **conserve** les quatre actuelles (Reçu, Source, Lignes, Statut) et on
  **ajoute** :
  - **Type** : une pastille `dmarc` / `tls`.
  - **Organisation émettrice** : `reporter` (« — » si absent). La colonne « Source »
    (transport imap/xml) est **conservée** à côté, à la demande.
  - **Volume + taux d'échec** : `failing_units / total_units`. Quand `total_units` est
    `null` (magnitude illisible), afficher « — » et non « 0 % » ; quand le total est un
    minorant (compteur partiel), préfixer « au moins » — même convention que les panneaux
    existants (`IpPanel`, `MtaStsPanel`).

## Erreurs et dégradation

| Situation | Comportement |
|---|---|
| Rapport sans `reporter` lisible | `reporter = null` → « — » en colonne. Pas d'échec. |
| Compteurs illisibles (`null`) | `total_units`/`failing_units` = `null` → « — » au taux, jamais « 0 % ». |
| Total partiel (au moins un compteur illisible) | Volume affiché comme minorant (« au moins N »). |
| Ancien rapport non encore backfillé | Ne se produit pas : le backfill est dans la migration, appliquée au déploiement avant que l'API serve. |
| `format` non préservé par `normalize()` | Repli sur le contenu des lignes pour dériver `kind` (voir §1). |

## Tests / vérification

- **Back-end** :
  - Tests unitaires de `summarize()` : DMARC (volume + échec), TLS (sessions, cas `null`/
    illisible/partiel), en-tête absent.
  - Test du filtre `?kind=` sur `GET /reports`.
  - **Test d'isolation cross-tenant maintenu** (bloquant) : les nouvelles colonnes n'ouvrent
    aucune fuite ; un tenant ne voit que ses rapports.
  - `pytest` complet + `ruff` verts.
- **Front-end** (pas de harnais de test — cf. autres specs) : `tsc -b` + `vite build` verts,
  puis **contrôle réel navigateur** : les onglets filtrent bien par type, les colonnes
  affichent le bon émetteur et un taux d'échec juste (dont les cas « — » et « au moins N »).

## Ce qu'on ne fait pas dans ce cycle, délibérément

- **La vue détail** (bandeau de synthèse, regroupement/tri des lignes, liens d'enquête
  renforcés) — c'est le **cycle 2**, qui réutilise la fondation posée ici.
- **Les filtres par date / par domaine / le tri des colonnes de la liste** — non retenus par
  l'utilisateur pour ce lot. YAGNI.
- **Séparer le menu latéral en deux entrées** — l'utilisateur a choisi des onglets dans la
  page, le menu reste inchangé.
- **Une colonne « Période couverte » dans la liste** — la donnée est stockée (pour le cycle
  2) mais pas affichée ici (non retenue).
