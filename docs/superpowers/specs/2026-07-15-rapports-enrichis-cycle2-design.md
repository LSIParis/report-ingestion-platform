# Rapports enrichis — Cycle 2 : vue détail (synthèse, groupement, liens d'enquête)

**Date** : 2026-07-15
**Statut** : validé, prêt pour le plan d'implémentation

## Le problème

La vue détail d'un rapport (`ReportDetail.tsx`) est aujourd'hui minimale : un en-tête réduit
(id, `source_type · N lignes · statut`, bouton « Rejouer le parsing »), deux onglets
(Données / Erreurs), et des tables par type (DMARC ligne par enregistrement, TLS
sessions + échecs, générique). Pour comprendre un rapport, il faut lire toutes ses lignes.

Le **cycle 2** enrichit cette vue selon trois axes validés : un **bandeau de synthèse** en
tête, une **vue groupée par IP** pour les rapports DMARC, et des **liens d'enquête
renforcés**. Il s'appuie sur la fondation du **cycle 1** (colonnes de résumé dénormalisées
sur `report`, déjà exposées par `ReportOut` : `kind`, `reporter`, `total_units`,
`failing_units`, `units_partial`, `period_start`, `period_end`).

## Architecture

### Décision : un endpoint d'analyse unique

Le bandeau DMARC a besoin de deux agrégats par rapport (répartition DKIM/SPF alignée, domaine)
et la vue groupée a besoin des lignes agrégées par IP — tout cela sur les **mêmes lignes** du
rapport. On expose donc **un seul** endpoint, plutôt que deux qui rescannent les mêmes lignes.
Le **verdict TLS** ne demande aucune agrégation nouvelle : il se **dérive côté client** des
champs déjà exposés par le cycle 1.

### 1. Back-end — `GET /reports/{id}/breakdown`

RLS-scopé (via `get_db`, session déjà scopée — **aucun** `WHERE tenant_id` applicatif),
accessible à **tous** les utilisateurs du tenant. Réponse **kind-aware** :

- **Commun** : `{ "policy_domain": str | null }` — lu dans les lignes du rapport (`data->>'policy_domain'`, uniforme sur tout le rapport).
- **DMARC** (`report.kind == 'dmarc'`), en plus :
  ```json
  {
    "dkim_aligned": int | null,
    "spf_aligned": int | null,
    "sources": [{ "source_ip": str, "messages": int, "compliant": int, "failing": int }]
  }
  ```
  - `dkim_aligned = Σ message_count où data->>'dkim' == 'pass'` ; `spf_aligned = Σ … où data->>'spf' == 'pass'`.
  - `sources` : lignes groupées par `data->>'source_ip'`, `messages = Σ message_count`,
    `compliant = Σ message_count où aligned == 'pass'`, `failing = messages - compliant`,
    triées par `messages` décroissant.
  - Mêmes casts JSONB que `app/api/metrics.py` (`message_count` en `Integer`, sommes
    conditionnelles), mais **filtrés sur ce `report_id`**.
- **TLS** (`report.kind == 'tls'`) : pas de champs supplémentaires (`dkim_aligned`/`spf_aligned`
  et `sources` absents ou nuls). Le verdict TLS se dérive côté client (voir §4).
- **404** si le rapport n'existe pas / n'est pas visible du tenant (jamais 403).

**Discipline « null ≠ 0 »** : `dkim_aligned`/`spf_aligned`/`messages` reposent sur `message_count`.
Le `message_count` DMARC est requis par le profil (`_default_dmarc_xml.json`, `required: true`),
donc lisible en pratique ; par cohérence avec le reste de la plateforme, l'agrégat SQL
`SUM(CAST(... AS Integer))` ignore les valeurs nulles (`NULL` non compté, jamais lu comme 0).

### 2. Back-end — filtre `GET /reports?reporter=<org>`

`list_reports` gagne un paramètre `reporter: str | None = None` ; `if reporter: q = q.filter(Report.reporter == reporter)` (correspondance exacte — le lien « même émetteur » passe la valeur exacte de `reporter`). Se combine avec `status_f`, `kind`, `brand`. Réutilise la colonne `report.reporter` du cycle 1 — trivial, aucune agrégation.

### 3. Front-end — bandeau de synthèse (`ReportDetail.tsx`)

Un bandeau **kind-aware** remplace l'en-tête minimal actuel. Il combine deux sources :
`useReport` (cycle 1 : émetteur, type, période, volume, taux d'échec) et un nouveau
`useReportBreakdown(id)` (domaine, et pour DMARC la répartition DKIM/SPF).

- **Commun** : émetteur, badge type (DMARC/TLS), période (`period_start → period_end`),
  volume total, taux d'échec (`failing_units / total_units`, convention « — »/« au moins N »
  du cycle 1), domaine.
- **DMARC** : deux barres — **DKIM aligné** et **SPF aligné** (`dkim_aligned`/`spf_aligned`
  rapportés au volume) — pour voir lequel réparer.
- **TLS** : un **verdict** dérivé de `ReportOut` — `total_units !== null && total_units > 0 && !units_partial && failing_units == 0`
  ⇒ « chiffrement vérifié, sûr de passer en enforce » (vert) ; sinon « des sessions échouent
  ou sont illisibles — à corriger avant d'appliquer » (rouge/orange). Le garde `total_units > 0`
  est essentiel : zéro session observée n'est PAS une preuve de succès (silence ≠ sûreté,
  même doctrine que `MtaStsPanel.TlsVerdict`).
- **Émetteur cliquable** → navigue vers `/reports?reporter=<émetteur>`.
- **Domaine cliquable — admin uniquement** (`isAdmin()`) → ouvre le `MtaStsPanel` existant
  (`{ tenantId: tenant actif, domain: policy_domain, onClose }`). Le panneau et son endpoint
  `/admin/tenants/{id}/tls-posture` sont admin ; pour un lecteur simple, le domaine reste du
  texte. Le bouton « Rejouer le parsing » actuel est conservé.

### 4. Front-end — onglet Données

- **DMARC** : la liste brute paginée (`DmarcTable`) est **remplacée** par une **vue groupée
  par IP** alimentée par `breakdown.sources` : une ligne par IP source (IP · messages ·
  conformes · en échec), triée par volume, **IP cliquable** → `IpPanel`. Colonnes cohérentes
  avec le tableau « Sources d'envoi » de la Vue d'ensemble. La pagination par ligne
  disparaît (le groupement rend la liste courte).
- **TLS** : **inchangé** (`TlsTable` : sessions + échecs, IP du MTA déjà cliquable).
- **Générique** (CSV/XLSX/PDF) : inchangé.

### 5. Front-end — liste (`ReportsList.tsx`)

Lit `?reporter=` depuis l'URL, le passe à `useReports`, et affiche une **puce**
« Émetteur : X ✕ » qui, cliquée, retire le filtre (via le helper `set` existant). Cohérent
avec la façon dont les onglets/filtres actuels vivent dans les *search params*.

## Fichiers touchés (indicatif)

| Fichier | Rôle |
|---|---|
| `backend/app/api/reports.py` | **Modifier.** Endpoint `breakdown` + param `reporter`. |
| `backend/app/api/schemas.py` | **Modifier.** Schémas de réponse `breakdown` (le cas échéant). |
| `backend/tests/test_report_breakdown.py` | **Créer.** Agrégats DMARC, domaine, TLS minimal. |
| `backend/tests/test_reports_reporter_filter.py` | **Créer.** Filtre `reporter` (ou étendre un test liste existant). |
| `frontend/src/api/reports.ts` | **Modifier.** `useReportBreakdown`, param `reporter` de `useReports`, types. |
| `frontend/src/pages/ReportDetail.tsx` | **Modifier.** Bandeau de synthèse + vue groupée DMARC. |
| `frontend/src/pages/ReportsList.tsx` | **Modifier.** Puce filtre émetteur. |

## Erreurs et dégradation

| Situation | Comportement |
|---|---|
| Magnitude illisible (`total_units` null) | « — » au taux, jamais « 0 % ». Barres DKIM/SPF masquées si le volume est inconnu. |
| Rapport sans lignes | `sources` vide → « Aucune source » ; domaine « — ». |
| `policy_domain` absent | Domaine « — », pas de lien. |
| Lecteur non-admin | Domaine non cliquable (texte) ; le reste du bandeau inchangé. |
| Rapport d'un autre tenant | 404 (RLS), jamais 403. |

## Isolation (invariant CLAUDE.md)

L'endpoint `breakdown` et le filtre `reporter` passent par la session déjà scopée (RLS) —
aucun `WHERE tenant_id` applicatif. Le test d'isolation cross-tenant reste vert et
**bloque le merge** ; on ajoute une vérification que `breakdown` d'un rapport d'un autre
tenant renvoie 404.

## Tests / vérification

- **Back-end** : `breakdown` sur un rapport DMARC (sources groupées, `dkim_aligned`/`spf_aligned`,
  domaine), sur un rapport TLS (domaine seul), 404 hors tenant ; filtre `?reporter=`. `pytest`
  complet + `ruff` verts, isolation maintenue.
- **Front-end** (pas de harnais de test) : `tsc -b` + `vite build` verts, puis **contrôle réel
  navigateur** : bandeau correct par type, DKIM/SPF alignés justes, vue groupée par IP
  (IP cliquable), verdict TLS, lien émetteur (filtre + puce), lien domaine admin.

## Dépendance au cycle 1

Ce cycle consomme les colonnes/champs du **cycle 1** (`kind`, `reporter`, `total_units`,
`failing_units`, `units_partial`, `period_start`, `period_end`). L'implémentation se branche
donc sur le cycle 1 **mergé** (ou sur sa branche) — à acter au moment du plan.

## Ce qu'on ne fait pas dans ce cycle, délibérément

- **Alertes liées au domaine** dans le bandeau — non retenu.
- **Garder la liste brute DMARC** en plus du groupé — non retenu (le groupé remplace).
- **Dénormaliser `policy_domain`** sur `report` — inutile ici (lu à la volée par `breakdown`),
  on évite une migration + backfill de plus.
- **Tri de colonnes arbitraire** sur les tables — le groupé est déjà trié par volume ; pas de
  tri interactif dans ce cycle.
- **Rendre `MtaStsPanel` accessible aux non-admins** — c'est un composant d'édition admin ;
  hors périmètre.
