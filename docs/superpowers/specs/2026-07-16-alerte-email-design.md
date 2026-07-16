# E-mail sortant — Cycle 2 : canal d'alerte e-mail (destinataire par tenant)

**Date** : 2026-07-16
**Statut** : validé, prêt pour le plan d'implémentation

## Le problème

Les alertes partent aujourd'hui par **un seul canal actif** (`ALERT_CHANNEL`) : `webhook`
(POST JSON) ou `desk365` (ticket helpdesk). On veut un **canal e-mail** : quand
`ALERT_CHANNEL=email`, chaque alerte est envoyée par e-mail au **destinataire propre du
domaine concerné**. Il réutilise la couche `app/services/mailer.py` construite au cycle 1.

## Architecture

Le système d'alertes appelle `get_channel().envoyer(event, alert, tenant)` sans rien savoir
du canal ; ajouter un canal = un fichier dans `channels/` + une entrée dans `_CANAUX`
(`channels/__init__.py`). Contrat existant, respecté à l'identique :

- `envoyer(event, alert, tenant) -> bool` : renvoie `True` si envoyé, `False` si le canal
  **n'est pas configuré** (jamais un silence muet — on le journalise).
- lève une sous-classe de `CanalIndisponible` si le canal est configuré mais l'envoi
  **échoue** : c'est la tâche Celery qui décide de retenter, pas le canal.

### 1. Destinataire par tenant — `tenant.alert_email` (migration `0013`)

Une colonne `alert_email` (`Text`, nullable) sur `tenant`. Elle accepte **une ou plusieurs
adresses séparées par des virgules** (un seul champ, souple : `a@x.fr, b@y.fr`). `NULL` ou
vide = **pas d'alerte e-mail** pour ce domaine.

### 2. Le canal — `app/services/alerting/channels/email.py`

```
envoyer(event: str, alert, tenant) -> bool
```

- Destinataires = `tenant.alert_email` découpé sur les virgules, chaque adresse *strippée*,
  les vides ignorés. Aucun destinataire → `log.warning("alerting.email_non_configure",
  alert_event=event, kind=alert.kind, domain=tenant.domain)` + `return False`.
- Sujet : `[DMARC] {tenant.domain} — {nature}` (même famille de libellés que le canal
  Desk365 : une table `kind -> libellé lisible`, repli sur `alert.kind`).
- Corps (français, texte simple) : domaine ; type + sévérité (`{alert.kind} ({alert.severity})`) ;
  les lignes de `alert.payload` (`  - clé : valeur`) ; et l'**état** —
  « Alerte OUVERTE » ou « Alerte RÉSOLUE » selon `event`.
- `send_email(adresse, sujet, corps)` pour **chaque** destinataire (à la suite).
- `EmailNonEnvoye` (SMTP non configuré / échec) → lève `EmailIndisponible(CanalIndisponible)`
  → la tâche Celery retente (aucune alerte perdue en silence). Sinon `return True`.
- **Envoi sur ouverture ET résolution** (les deux `event`), comme le canal webhook.

Enregistré `"email"` dans `_CANAUX` (`channels/__init__.py`).

### 3. Sélection

`ALERT_CHANNEL=email` suffit (le registre le rend actif). **Un seul canal à la fois**, comme
aujourd'hui — `email` remplace `webhook`/`desk365` quand il est choisi. **Aucune config
globale de destinataire** : le destinataire est **par tenant** (`alert_email`). Les réglages
SMTP (`SMTP_*`) sont ceux du cycle 1.

### 4. Édition admin — page Domaines

`alert_email` devient éditable **sur la page Domaines** (`Domains.tsx`, là où se gèrent les
tenants et MTA-STS) : extension de `PATCH /admin/tenants/{id}` (`TenantPatch` + sérialisation)
et du formulaire côté front. Une validation légère (chaque fragment séparé par virgule
contient un `@`) ; champ optionnel.

## Erreurs et dégradation

| Situation | Comportement |
|---|---|
| Tenant sans `alert_email` | Non envoyé, **journalisé** (`email_non_configure`), `return False` — jamais de plantage. |
| SMTP indisponible / envoi échoué | `EmailIndisponible` (sous-classe de `CanalIndisponible`) → Celery retente. Une alerte n'est jamais perdue en silence. |
| Plusieurs destinataires, un échoue | L'échec lève `EmailIndisponible` (retry) — le comportement « au moins un échec = on retente » reste sûr (au pire, un doublon à la reprise, jamais une alerte manquée). |
| `ALERT_CHANNEL=email` mais aucun tenant n'a d'`alert_email` | Chaque envoi renvoie `False` (journalisé) ; le reste du pipeline d'alertes fonctionne. |

## Isolation

Le balayage d'alertes et l'ingestion tournent déjà en plan **worker (BYPASSRLS)** — le canal
ne fait qu'envoyer, il ne lit aucune donnée d'un autre tenant. `alert_email` vit sur `tenant`
(déjà une table métier, mais accédée ici via le worker). L'édition passe par les routes
**admin** existantes. Le test d'isolation cross-tenant reste vert et inchangé.

## Tests / vérification

- **Back-end** (`send_email` **moqué** — jamais de vrai SMTP) :
  - `envoyer` sans `alert_email` → `False`, mailer non appelé, log émis.
  - avec un destinataire → `send_email` appelé une fois, sujet `[DMARC] domaine — …`, corps
    contenant type/sévérité/message et « OUVERTE » (event ouverture) ou « RÉSOLUE » (fermeture).
  - avec **deux** destinataires → `send_email` appelé deux fois.
  - `EmailNonEnvoye` du mailer → `envoyer` lève `EmailIndisponible` (sous-classe de
    `CanalIndisponible`).
  - `get_channel()` avec `ALERT_CHANNEL=email` renvoie le module `email`.
  - `PATCH /admin/tenants/{id}` met à jour `alert_email` ; la sérialisation le renvoie.
  - `pytest` complet + `ruff check app scripts tests` verts ; isolation verte.
- **Front-end** : `tsc -b` + `vite build` verts, puis **contrôle réel** : renseigner un
  destinataire sur un domaine ; avec `ALERT_CHANNEL=email` + `SMTP_*` réglés, une alerte
  ouverte arrive par e-mail au bon destinataire.

## Ce qu'on ne fait pas, délibérément

- **Envoyer sur plusieurs canaux à la fois** — le modèle reste « un canal actif »
  (`ALERT_CHANNEL`). Multi-canal = un autre chantier.
- **Un destinataire global** (type `ALERT_EMAIL_TO`) — non : le destinataire est **par tenant**.
- **Gabarits HTML / pièces jointes** — corps texte simple, comme les tickets Desk365.
- **Rate-limiting des envois** — la déduplication des alertes (réconciliateur) borne déjà le
  volume ; hors périmètre.
