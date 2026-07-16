# E-mail sortant — Cycle 1 : mailer SMTP + vérification du changement d'e-mail

**Date** : 2026-07-15
**Statut** : validé, prêt pour le plan d'implémentation

## Le problème

Un utilisateur peut aujourd'hui changer **son propre** e-mail (identifiant de connexion) et
le changement s'applique **immédiatement** en base, sans preuve qu'il possède la nouvelle
adresse (branche `feat/fiche-utilisateur`, `PATCH /auth/me`). On veut **vérifier
l'ownership** avant d'appliquer : la plateforme envoie un **code** à la nouvelle adresse, et
le changement n'est écrit qu'une fois le code confirmé.

Prérequis : la plateforme ne sait pas envoyer d'e-mail sortant. Ce cycle construit donc
d'abord une **couche d'envoi SMTP** (réutilisée au cycle 2 par le canal d'alerte e-mail).

Ce lot se construit **sur la branche `feat/fiche-utilisateur`** (en attente, non mergée) et
fusionnera avec elle.

## Architecture

### 1. Couche d'envoi SMTP — `app/services/mailer.py`

Une fonction `send_email(to: str, subject: str, body: str) -> None` sur `smtplib`
(connexion SMTP + STARTTLS + login + `sendmail`). Configurée par de nouveaux réglages
(`app/config.py`) : `smtp_host`, `smtp_port` (défaut 587), `smtp_user`, `smtp_password`,
`smtp_from` (ex. `no-reply@lsiparis.tech`). Injectés dans la stack (worker + api).

- **SMTP non configuré** (`smtp_host` vide) → `send_email` lève une exception dédiée
  `EmailNonEnvoye` ; l'appelant la traduit en erreur claire, jamais un plantage silencieux.
- **Échec réseau/SMTP** → même `EmailNonEnvoye` (journalisée avec le motif, sans le corps).
- Générique par conception (le cycle 2 l'utilise pour les alertes) mais **ici, seul l'e-mail
  de vérification l'appelle**.

### 2. État « changement en attente » sur `app_user` (migration `0012`)

Quatre colonnes nullables :

| Colonne | Rôle |
|---|---|
| `pending_email` | La nouvelle adresse à confirmer (déjà normalisée). |
| `email_code_hash` | Le code **haché** (jamais en clair), via `hash_password`/`verify_password` existants. |
| `email_code_expires_at` | Expiration du code (`DateTime(timezone=True)`). |
| `email_code_attempts` | Compteur d'essais de saisie (`Integer`, défaut 0), pour borner le brute-force. |

Tant que le code n'est pas confirmé, **`app_user.email` (l'identifiant de connexion) ne
change pas**. L'état en attente est purgé après confirmation, ou simplement laissé expirer.

### 3. Deux endpoints self (le changement d'e-mail sort de `PATCH /auth/me`)

Routeur `/auth` (`app/auth/login.py`), résolution du compte par `ctx.user` (jeton signé) :

- **`POST /auth/me/email/request`** `{ "new_email": str }` :
  - `normalize_email` (minuscule + `@`) ; si `new_email == email actuel` → 400 (rien à
    changer) ; unicité (aucun **autre** compte ne l'a) → **409** sinon.
  - Génère un **code à 6 chiffres** (`secrets.randbelow(1_000_000)`, formaté `%06d`).
  - **Envoie le code** à `new_email` (sujet + corps en français) **d'abord**. Échec d'envoi
    → `EmailNonEnvoye` → **502** « Impossible d'envoyer le code, réessayez. » et **rien n'est
    écrit** (pas d'état en attente orphelin dont le code n'aurait jamais été reçu).
  - Envoi réussi → pose `pending_email` + `email_code_hash` + `email_code_expires_at =
    now + 15 min` + `email_code_attempts = 0`, commit → **202**. Un renvoi (nouvel appel)
    régénère le code, écrase l'état précédent et ré-arme le compteur.
- **`POST /auth/me/email/confirm`** `{ "code": str }` :
  - Aucun changement en attente / expiré → **400** « Aucun changement d'e-mail en attente ou
    code expiré. »
  - `email_code_attempts >= 5` → **429** « Trop d'essais, redemandez un code. »
  - Code incorrect → incrémente `email_code_attempts`, commit, **400** « Code incorrect. »
  - Code correct **et** `pending_email` **toujours** libre (re-vérifié — course) → applique
    (`user.email = pending_email`), purge l'état en attente, commit → **204**. Si
    `pending_email` a été pris entre-temps → **409**, purge l'état.
  - Le front, après 204, **efface la session et redirige `/login`** (le jeton portait
    l'ancien e-mail).

### 4. `PATCH /auth/me` et l'admin

- **`PATCH /auth/me`** ne modifie **plus** l'e-mail : `ProfileIn` **perd le champ `email`** ;
  l'endpoint met à jour uniquement les 5 champs d'identité (immédiat). `GET /me` continue de
  renvoyer l'e-mail (+ un `pending_email` pour l'affichage « en attente », si présent).
- **`PATCH /admin/users/{id}`** : **inchangé**. L'admin change l'e-mail d'un compte
  immédiatement (non vérifié) — décision assumée (provisionnement, admin de confiance).

### 5. Front — `ProfileDialog` (self)

La modale sépare **identité** et **e-mail** en mode `self` :

- **Identité** (nom, prénom, société, adresse, téléphone) : bouton « Enregistrer » →
  `PATCH /auth/me` (immédiat).
- **E-mail** : l'adresse actuelle est affichée avec un bouton **« Changer l'e-mail »** →
  saisie de la nouvelle adresse → `POST …/email/request`. La modale bascule alors sur un
  **écran code** : « Un code a été envoyé à *nouvelle@adresse* » + champ code + « Confirmer »
  → `POST …/email/confirm`. Succès → **`clearSession()` + `nav("/login")`**. Un bouton
  « Renvoyer le code » relance `request`.

En mode `admin`, l'e-mail reste éditable et **immédiat** (via `PATCH /admin/users/{id}`,
inchangé) — l'admin ne reçoit pas de code à la place du tiers.

## Sécurité / erreurs

| Situation | Comportement |
|---|---|
| Code haché en base | Jamais stocké/loggué en clair (`verify_password`). |
| Force brute du code | 6 chiffres + expiration 15 min + **5 essais max** → 429. |
| Nouvelle adresse prise (au `request` ou au `confirm`) | 409 ; au confirm, l'état en attente est purgé. |
| SMTP indisponible / échec d'envoi | 502 « Impossible d'envoyer le code » ; jamais de plantage. |
| Code expiré / aucun en attente | 400 message clair. |
| Après confirmation | Session effacée + reconnexion avec la nouvelle adresse. |
| Énumération d'e-mails | Le 409 sur une adresse prise révèle l'existence, mais l'appel est **authentifié** (l'utilisateur choisit sa propre adresse) — risque assumé, mineur. |

## Sécurité / isolation

- Les endpoints `email/request` et `email/confirm` ne touchent **que** le compte porteur du
  jeton (`ctx.user`) — jamais un autre compte, jamais `role`/`tenant_ids`. Aucune élévation
  de privilège.
- `app_user` n'est pas tenant-scoped (table d'auth) → aucune RLS concernée. Le test
  d'isolation cross-tenant reste vert et bloquant (inchangé).

## Tests / vérification

- **Back-end** (l'envoi SMTP est **moqué** dans les tests — on ne parle jamais à un vrai
  serveur) :
  - `request` : normalise, refuse l'e-mail identique (400) et l'e-mail pris (409), pose l'état
    en attente, appelle le mailer (espionné) ; l'e-mail de connexion **ne bouge pas**.
  - `confirm` : bon code → applique + purge (204) ; mauvais code → incrémente + 400 ;
    5 essais → 429 ; code expiré / aucun en attente → 400 ; adresse reprise entre-temps → 409.
  - `PATCH /auth/me` **ne modifie plus l'e-mail** (le champ a disparu de `ProfileIn`).
  - `mailer.send_email` : SMTP non configuré → `EmailNonEnvoye` (sans appel réseau).
  - `pytest` complet + `ruff check app scripts tests` verts ; isolation verte.
- **Front-end** (pas de harnais de test) : `tsc -b` + `vite build` verts, puis **contrôle réel
  navigateur** : changer son e-mail envoie un code, un mauvais code est refusé, le bon code
  applique + déconnecte ; l'identité s'enregistre toujours immédiatement ; l'admin change un
  e-mail sans code.

## Ce qu'on ne fait pas dans ce cycle, délibérément

- **Le canal d'alerte e-mail** — c'est le **cycle 2** (réutilise `mailer.py`).
- **Lien de confirmation** (au lieu du code) — non retenu (le code reste en session).
- **Vérification du changement d'e-mail côté admin** — l'admin reste immédiat.
- **Rate-limiting fin des renvois** (au-delà de l'écrasement + expiration) — YAGNI ; un renvoi
  régénère le code, l'expiration et le plafond d'essais bornent l'abus.
- **Vérifier l'e-mail à la création de compte** — hors périmètre (seulement le changement).
