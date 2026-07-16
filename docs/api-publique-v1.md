# API publique v1 — Guide d'accès externe

Cette API permet à des programmes tiers d'interroger la plateforme DMARC et de créer des
domaines, via des **clés API**. Elle respecte l'isolation multitenant : une clé par-domaine
ne voit **que** son domaine.

- **URL de base** : `https://dmarc-reports.lsiparis.tech/api/v1`
- **Format** : JSON (requêtes et réponses).
- **Documentation interactive (OpenAPI / Swagger)** : `https://dmarc-reports.lsiparis.tech/api/docs`
  — schéma brut : `https://dmarc-reports.lsiparis.tech/api/openapi.json`

---

## 1. Obtenir une clé

Les clés se créent dans le dashboard, par un administrateur :

**Paramètres → section « Clés API » → Créer une clé.**

Deux types :

| Type | Préfixe | Ce qu'elle peut faire |
|---|---|---|
| **Plateforme** | `sk_plat_…` | Lire **tous** les domaines, créer des domaines, lire la quarantaine. |
| **Par-domaine** | `sk_dom_…` | Lire **uniquement** son propre domaine (rapports, métriques). Lecture seule. |

> ⚠️ **Le secret ne s'affiche qu'une seule fois**, à la création. Copiez-le immédiatement et
> stockez-le dans un coffre / une variable d'environnement. Il n'est jamais réaffiché ni
> récupérable ensuite. En cas de perte ou de fuite : révoquez la clé et créez-en une neuve.

---

## 2. S'authentifier

Chaque requête porte la clé dans l'en-tête `Authorization`, en `Bearer` :

```
Authorization: Bearer sk_plat_VotreSecretIci
```

Exemple minimal :

```bash
curl -H "Authorization: Bearer sk_plat_VotreSecretIci" \
     https://dmarc-reports.lsiparis.tech/api/v1/domains
```

Une clé ne peut appeler **que** les routes `/api/v1/…`. Toute autre URL de la plateforme lui
est interdite (`403`).

---

## 3. Endpoints

### `GET /api/v1/domains` — liste des domaines

Clé par-domaine → son seul domaine. Clé plateforme → tous les domaines.

```bash
curl -H "Authorization: Bearer sk_dom_…" \
     https://dmarc-reports.lsiparis.tech/api/v1/domains
```

Réponse `200` :

```json
[
  {
    "id": "3f1c…",
    "domain": "exemple.com",
    "name": "Exemple SA",
    "status": "active",
    "reports": 128,
    "last_report_at": "2026-07-15T00:00:00+00:00",
    "alert_email": "ops@exemple.com"
  }
]
```

### `GET /api/v1/reports` — agrégats DMARC

Paramètre optionnel `days` (fenêtre en jours, défaut `30`, max `365`).

```bash
curl -H "Authorization: Bearer sk_dom_…" \
     "https://dmarc-reports.lsiparis.tech/api/v1/reports?days=30"
```

Réponse `200` :

```json
{
  "days": 30,
  "messages": 15230,
  "compliant": 14980,
  "failing": 250,
  "compliance_rate": 98.4,
  "dkim_pass": 15010,
  "spf_pass": 14800,
  "quarantined": 12,
  "rejected": 3,
  "sources": 47,
  "failing_sources": 5
}
```

`compliance_rate` vaut `null` s'il n'y a aucun message sur la fenêtre (rien à mesurer).

### `GET /api/v1/metrics` — série temporelle

Volume quotidien conforme / en échec. Paramètre optionnel `days` (défaut `30`, max `365`).

```bash
curl -H "Authorization: Bearer sk_dom_…" \
     "https://dmarc-reports.lsiparis.tech/api/v1/metrics?days=7"
```

Réponse `200` :

```json
[
  { "day": "2026-07-14", "compliant": 512, "failing": 8 },
  { "day": "2026-07-15", "compliant": 498, "failing": 3 }
]
```

### `GET /api/v1/quarantine` — rapports non attribués · clé plateforme uniquement

Rapports arrivés avant que leur domaine n'existe (non rattachés à un client).

```bash
curl -H "Authorization: Bearer sk_plat_…" \
     https://dmarc-reports.lsiparis.tech/api/v1/quarantine
```

Réponse `200` :

```json
[
  {
    "id": "9a2b…",
    "message_id": "<abc@reporter.example>",
    "from_address": "noreply@google.com",
    "subject": "Report Domain: inconnu.com",
    "received_at": "2026-07-15T08:12:00+00:00"
  }
]
```

### `POST /api/v1/domains` — créer un domaine · clé plateforme uniquement

Inscrit un nouveau domaine à surveiller (crée le tenant + la règle de reconnaissance). La
procédure DNS reste à poser ensuite côté client.

```bash
curl -X POST \
     -H "Authorization: Bearer sk_plat_…" \
     -H "Content-Type: application/json" \
     -d '{"domain": "nouveau-client.fr", "name": "Nouveau Client"}' \
     https://dmarc-reports.lsiparis.tech/api/v1/domains
```

- `domain` (requis) : un nom de domaine (`exemple.com`), pas une adresse e-mail.
- `name` (optionnel) : nom lisible du client (défaut : le domaine).

Réponse `201` :

```json
{ "id": "7d4e…", "domain": "nouveau-client.fr", "name": "Nouveau Client" }
```

---

## 4. Scoper une clé plateforme à un domaine (optionnel)

Une clé plateforme voit tout par défaut. Pour restreindre une lecture à **un** domaine,
ajoutez l'en-tête `X-Tenant-Id` avec l'`id` du domaine (obtenu via `GET /api/v1/domains`) :

```bash
curl -H "Authorization: Bearer sk_plat_…" \
     -H "X-Tenant-Id: 3f1c…" \
     "https://dmarc-reports.lsiparis.tech/api/v1/reports?days=30"
```

---

## 5. Erreurs

| Code | Signification |
|---|---|
| `401` | En-tête `Authorization` absent, ou clé inconnue / **révoquée**. |
| `403` | Clé utilisée hors de `/api/v1`, **ou** clé par-domaine tentant une action réservée à la plateforme (`POST /api/v1/domains`, `GET /api/v1/quarantine`). |
| `409` | `POST /api/v1/domains` : ce domaine est déjà surveillé. |
| `422` | Corps de requête invalide (ex. domaine mal formé). |

Le corps d'erreur est de la forme `{ "detail": "message" }`.

---

## 6. Bon à savoir

- **Révocation** : une clé révoquée depuis le dashboard cesse immédiatement de fonctionner
  (`401`). La révocation est définitive ; recréez une clé au besoin.
- **Pas de limite de débit (rate-limiting)** pour l'instant : restez raisonnable sur la
  fréquence d'appel. Une clé compromise doit être révoquée sans délai.
- **Isolation** : une clé par-domaine ne peut structurellement pas accéder aux données d'un
  autre domaine — la restriction est appliquée en base (RLS), pas seulement dans le code.
- **HTTPS obligatoire** : n'appelez jamais l'API en clair ; la clé transiterait en clair.
