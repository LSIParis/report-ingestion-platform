# Fiche de détail utilisateur

**Date** : 2026-07-15
**Statut** : validé, prêt pour le plan d'implémentation

## Le problème

Un compte (`app_user`) ne porte aujourd'hui que son `email` (identifiant de connexion),
son `password_hash`, son `role` et sa date de création. On veut lui associer une **fiche
d'identité** : nom, prénom, société, adresse, téléphone, e-mail. Elle doit être remplie et
modifiée par l'**administrateur** (pour n'importe quel compte, depuis la page
**Paramètres — comptes**) **et** par **chaque utilisateur pour lui-même** (via une entrée
**« Mon profil »**).

## Architecture

### 1. Données — `app_user`

Cinq colonnes nouvelles, toutes **nullables** (`Text`) : `first_name`, `last_name`,
`company`, `address`, `phone`. L'`email` existe déjà. Une migration Alembic les ajoute ;
**pas de backfill** — `NULL` signifie « non renseigné ».

`app_user` est la table d'**authentification** (rattachée aux domaines via `UserTenant`),
**pas** une table métier tenant : elle **ne porte pas** `tenant_id` et **n'est pas** sous
RLS. Les champs profil suivent donc le même régime d'accès que `email`/`role` (gérés par les
routes admin et la route « moi »), sans policy RLS.

### 2. Back-end

Le JWT porte `sub = email` (voir `auth/login.py`) et le middleware **fait confiance au
jeton signé** — il ne re-résout pas l'utilisateur en base à chaque requête. `ctx.user` est
donc l'e-mail du jeton. Deux chemins d'écriture :

- **Admin — `PATCH /admin/users/{id}`** (endpoint existant, admin) : étendu pour accepter
  les cinq champs d'identité **et** l'`email`. L'e-mail est mis en **minuscules** (comme au
  login) et son **unicité** est vérifiée → **409** s'il est déjà pris par un autre compte.
  Le rôle et les domaines restent gérés par ce même endpoint comme aujourd'hui.
- **Soi-même — `PATCH /me`** (nouveau) : résout le compte par `ctx.user` (e-mail du jeton) et
  met à jour **uniquement l'identité** (les six champs). Il **n'accepte jamais** `role` ni
  `tenant_ids` — un utilisateur ne peut pas élargir ses propres droits. Même règle e-mail
  (minuscule + unicité, 409).
- **`GET /me`** (existant) : `MeOut` renvoie désormais aussi les cinq champs (+ l'e-mail déjà
  présent), pour pré-remplir la fiche.

**Validation** : les cinq champs profil sont **optionnels** (chaîne vide → `NULL`) ;
l'`email` reste **requis** et doit contenir `@`.

**Journalisation** : chaque mise à jour passe par `audit()` (comme les autres mutations
compte), actor = `ctx.user`.

### 3. Front-end

- **« Mon profil »** : une entrée ajoutée dans `AccountMenu` (le menu compte en haut à
  droite, **visible de tous**), à côté de « Changer mon mot de passe ». Elle ouvre un modal
  **`ProfileDialog`** (même patron que `PasswordDialog`) : les six champs pré-remplis depuis
  `useMe`, enregistrés via `PATCH /me`.
- **Admin — action « Fiche »** : sur chaque ligne de la page **Paramètres — comptes**, une
  action « Fiche » (à côté de « Modifier »/« Réinitialiser »/« Supprimer ») ouvre **le même
  formulaire d'identité**, mais enregistré via `PATCH /admin/users/{id}` pour ce compte-là.
  Identité (la fiche) et accès (rôle/domaines, gérés par « Modifier ») restent **deux
  concerns séparés**.
- **Changement de son propre e-mail** : puisque le jeton porte l'ancien e-mail (`sub`) et que
  le serveur fait confiance au jeton, une fois un changement d'e-mail réussi **sur sa propre
  fiche**, le front **efface la session (`clearSession`) et redirige vers `/login`** — l'
  utilisateur se reconnecte avec son nouvel e-mail. Un **admin** qui change l'e-mail d'un
  **autre** compte : aucun impact sur sa propre session (le compte cible se reconnectera avec
  le nouvel e-mail ; son éventuel jeton en cours reste signé-valide jusqu'à expiration).

## Erreurs et dégradation

| Situation | Comportement |
|---|---|
| E-mail déjà utilisé par un autre compte | 409 → message « Cet e-mail est déjà utilisé » ; la saisie n'est pas perdue. |
| Champ profil laissé vide | Enregistré `NULL` → affiché « — » / champ vide. |
| E-mail vide ou sans `@` | Refusé côté formulaire (bouton désactivé) ; le serveur revalide. |
| Échec réseau à l'enregistrement | Message d'erreur, le modal reste ouvert avec la saisie. |
| Changement de son propre e-mail réussi | Session effacée + redirection `/login` (reconnexion). |

## Sécurité / isolation

- `PATCH /me` ne modifie **que** le compte du porteur du jeton (résolu par `ctx.user`) et
  **jamais** `role`/`tenant_ids` — pas d'élévation de privilège possible par cette route.
- `app_user` n'étant pas tenant-scoped, aucune policy RLS n'est concernée ; le **test
  d'isolation cross-tenant** existant reste vert et bloquant (inchangé par cette
  fonctionnalité).
- Le changement d'e-mail de soi-même n'exige pas de re-saisie du mot de passe : c'est une
  action **authentifiée en self-service** (une session déjà compromise pourrait de toute
  façon tout faire). Durcissement possible plus tard, hors périmètre.

## Tests / vérification

- **Back-end** :
  - `PATCH /me` : met à jour les six champs de l'appelant ; **refuse** (ignore) `role`/
    `tenant_ids` ; e-mail déjà pris → 409 ; e-mail mis en minuscules.
  - `PATCH /admin/users/{id}` étendu : met à jour l'identité + e-mail d'un compte tiers ;
    unicité 409.
  - `GET /me` renvoie les cinq nouveaux champs.
  - `pytest` complet + `ruff check app scripts tests` verts ; isolation cross-tenant verte.
- **Front-end** (pas de harnais de test) : `tsc -b` + `vite build` verts, puis **contrôle
  réel navigateur** : « Mon profil » ouvre la fiche pré-remplie et enregistre ; l'action
  « Fiche » admin édite un autre compte ; changer son propre e-mail déconnecte et permet la
  reconnexion ; un e-mail déjà pris affiche le 409.

## Ce qu'on ne fait pas, délibérément

- **Édition du rôle/domaines depuis la fiche** — ça reste dans « Modifier » (accès ≠ identité).
- **Re-saisie du mot de passe pour changer l'e-mail** — hors périmètre (self-service authentifié).
- **Champs profil obligatoires** — ils sont optionnels (enrichissement).
- **Une page « Mon profil » dédiée (route)** — un modal depuis le menu compte suffit (YAGNI).
- **Photo / avatar, préférences, etc.** — hors périmètre ; six champs, rien de plus.
