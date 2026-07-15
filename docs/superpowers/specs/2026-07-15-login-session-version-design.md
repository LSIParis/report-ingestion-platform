# Login, session et version — polissage de la coquille

**Date** : 2026-07-15
**Statut** : validé, prêt pour le plan d'implémentation

## Le problème

Quatre demandes, toutes sur la coquille de l'application (login + shell authentifié) :

1. Une **image de fond** sur la page de login.
2. Un **copyright** sous la boîte de connexion.
3. Une **déconnexion automatique après 10 minutes d'inactivité**.
4. Un **numéro de version** consultable dans une boîte **« À propos »**, ouverte depuis la
   barre latérale.

Elles forment un lot cohérent : trois sont purement frontend, la quatrième ajoute un petit
branchement CI pour injecter le SHA du build. Un seul cycle spec → plan.

## Architecture

### 1. Fond d'écran du login

L'image (`frontend/src/assets/login-bg.jpg`, convertie et compressée pour le web depuis le
PNG fourni — une photo se compresse en JPEG, pas en PNG) couvre toute la page
(`bg-cover bg-center`). La boîte de connexion, centrée par-dessus, passe sur un fond
**blanc légèrement opaque avec un léger flou** (`bg-white/90 backdrop-blur`) : le formulaire
reste lisible sur la photo sombre. Le logo déjà en place (commit `d8725a9`) coiffe la boîte.

### 2. Copyright

Sous la boîte, centré, en petit texte clair (la photo de fond est sombre) :
**« © LSI-Maintenance {année} »**. L'année est **dynamique** (`new Date().getFullYear()`) —
elle affiche 2026 aujourd'hui et reste juste l'an prochain, sans rien à retoucher.

### 3. Déconnexion après 10 min d'inactivité

Un hook `useIdleLogout(minutes)` monté dans la coquille authentifiée (`Layout`, rendu pour
toutes les routes derrière `RequireAuth`). La page de login n'étant pas sous `Layout`, le
minuteur ne s'y déclenche pas.

Fonctionnement :

- **Horodatage de dernière activité dans `localStorage`** (`last_activity`), mis à jour
  (au plus une fois par seconde, throttle) sur `mousemove`, `mousedown`, `keydown`,
  `scroll`, `touchstart`.
- **Contrôle périodique** (`setInterval`, toutes les ~30 s) **et au retour sur l'onglet**
  (`visibilitychange`) : si `Date.now() - last_activity > 10 min`, on
  `clearSession()` + redirige vers `/login`.
- **Survit au rechargement et vaut pour tous les onglets** : l'horodatage étant dans
  `localStorage`, une activité dans n'importe quel onglet garde la session vivante partout,
  et un `storage` event réaligne les autres onglets.
- Le contrôle au `visibilitychange` couvre le cas du poste mis en veille : au réveil, si le
  délai est dépassé, déconnexion immédiate (le `setInterval` seul pourrait tarder de 30 s).

La constante `IDLE_MINUTES = 10` vit dans le hook. Le token JWT garde son plafond absolu
d'1h (`jwt_ttl_seconds`, inchangé) : le minuteur d'inactivité est une couche d'hygiène de
session côté client, pas un remplacement du contrôle serveur (que le middleware et la
gestion du 401 dans `api/client.ts` continuent d'assurer).

*Limite assumée* : un minuteur côté navigateur ne révoque pas le token côté serveur — un
token volé reste valide jusqu'à son expiration absolue (1h). C'est le compromis choisi
(cf. la question tranchée en brainstorming) : couvrir le poste laissé ouvert, sans monter
un mécanisme de rafraîchissement de token qui n'existe pas aujourd'hui.

### 4. Version et boîte « À propos »

**Source de la version** — deux morceaux :

- Le **numéro sémantique** que l'on incrémente dans `frontend/package.json`
  (`"version"`), lu au build.
- Le **SHA court du commit** du build, injecté par la CI.

**Injection au build** (`vite.config.ts`) :

```js
define: {
  __APP_VERSION__: JSON.stringify(pkg.version),
  __BUILD_SHA__: JSON.stringify(process.env.VITE_BUILD_SHA || "dev"),
}
```

Déclarés comme globaux TypeScript dans `vite-env.d.ts`. En local (sans variable), le SHA
vaut `"dev"` — honnête : on ne prétend pas connaître un commit qu'on n'a pas.

**Branchement CI** : `Dockerfile.prod` du frontend accepte `ARG VITE_BUILD_SHA` et le pose
en `ENV` avant `npm run build` ; `build-images.yml` passe `VITE_BUILD_SHA=${{ github.sha }}`
en `build_args` de l'image `report-frontend` (à côté du `VITE_API_URL=/api` déjà présent).

**La boîte « À propos »** (`components/About.tsx`) — un modal (même patron que
`MtaStsPanel` / `PasswordDialog`, qui existent déjà) affichant :

- le nom : *LSI-Maintenance Mail Dispatch* (avec le logo) ;
- **Version** : `__APP_VERSION__` ;
- **Build** : les 7 premiers caractères de `__BUILD_SHA__` ;
- **© LSI-Maintenance {année}**.

**Accès** : une entrée **« À propos »** ajoutée **en bas de la barre latérale** de `Layout`,
séparée des liens de navigation (poussée en bas via `mt-auto`). Ce n'est **pas une route** —
un bouton qui ouvre le modal (état local dans `Layout`). Visible pour **tous** les
utilisateurs (pas seulement les admins), contrairement aux liens admin.

## Fichiers touchés

| Fichier | Rôle |
|---|---|
| `frontend/src/assets/login-bg.jpg` | **Créer.** Photo de fond, compressée. |
| `frontend/src/pages/Login.tsx` | **Modifier.** Fond plein écran, carte semi-opaque, copyright. |
| `frontend/src/auth/useIdleLogout.ts` | **Créer.** Le hook d'inactivité. |
| `frontend/src/components/Layout.tsx` | **Modifier.** Monte `useIdleLogout(10)` ; entrée « À propos » en bas. |
| `frontend/src/components/About.tsx` | **Créer.** Le modal. |
| `frontend/vite.config.ts` | **Modifier.** `define` version + SHA. |
| `frontend/src/vite-env.d.ts` | **Modifier.** Déclarer les globaux `__APP_VERSION__` / `__BUILD_SHA__`. |
| `frontend/Dockerfile.prod` | **Modifier.** `ARG`/`ENV VITE_BUILD_SHA`. |
| `.github/workflows/build-images.yml` | **Modifier.** Passer `VITE_BUILD_SHA=${{ github.sha }}`. |

## Erreurs et dégradation

| Situation | Comportement |
|---|---|
| Image de fond qui ne charge pas | Le fond reste uni (couleur de repli sous l'image) ; la boîte de login reste lisible et fonctionnelle. |
| `localStorage` inaccessible (navigation privée stricte) | Le minuteur ne peut pas persister l'horodatage → repli sur un minuteur en mémoire (déconnexion après 10 min dans l'onglet courant). On ne casse jamais la session pour ça. |
| Pas de `VITE_BUILD_SHA` au build (local) | Le SHA affiché est `dev`. |
| Onglet en arrière-plan longtemps | Au retour (`visibilitychange`), contrôle immédiat : déconnexion si dépassé. |

## Tests / vérification

Le frontend n'a **pas** de harnais de test (ni vitest ni jest) — la vérification passe par
`tsc -b` + `vite build` verts, plus un **contrôle réel dans le navigateur**. Comme sur ce
projet on a vu ce que vaut le non-vérifié en vrai :

- **Fond + copyright** : la page de login affiche la photo, la boîte lisible par-dessus, le
  copyright en dessous.
- **Inactivité** : baisser temporairement `IDLE_MINUTES` (ex. à 0,2 min) pour observer la
  déconnexion réelle sans attendre 10 min, puis remettre 10. Vérifier aussi qu'une activité
  la repousse, et le réveil après veille de l'onglet.
- **À propos** : le bouton en bas de la barre ouvre le modal ; version et SHA corrects
  (SHA = `dev` en local, le vrai SHA après un build CI).

La logique d'inactivité doit rester dans une **fonction pure** testable (`est_inactif(dernier,
maintenant, limite) -> bool`) même sans runner : elle documente et isole la seule règle qui
compte, et rend un futur test trivial si un runner est ajouté.

## Ce qu'on ne fait pas, délibérément

- **Raccourcir le token JWT / mécanisme de refresh.** Le minuteur front couvre le besoin
  (poste laissé ouvert). Un refresh token est un chantier serveur à part, non demandé.
- **Rendre « À propos » configurable** (changelog, notes de version…). Un modal simple :
  nom, version, build, copyright. YAGNI.
- **Un vrai harnais de test frontend.** Hors périmètre ; on garde la logique d'inactivité en
  fonction pure pour ne pas fermer la porte.
- **Bloquer le login si l'image de fond manque.** Le fond est décoratif ; la connexion ne
  dépend jamais de lui.
