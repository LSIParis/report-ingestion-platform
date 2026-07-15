# Login, session et version — Plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Habiller le login (fond + copyright), déconnecter après 10 min d'inactivité, et rendre la version consultable dans une boîte « À propos » depuis la barre latérale.

**Architecture:** Quatre changements frontend cohérents. L'inactivité est un hook monté dans la coquille authentifiée, dont la règle vit dans une fonction pure. La version combine le numéro de `package.json` (lu au build) et le SHA du commit injecté par la CI, exposés en globaux via `vite.config`.

**Tech Stack:** React 19 · TypeScript · Vite · Tailwind · Docker (build frontend) · GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-07-15-login-session-version-design.md`

## Global Constraints

- **Aucune nouvelle dépendance npm.** React/Vite/Tailwind suffisent ; le traitement d'image se fait avec Pillow **côté conteneur backend** (déjà présent), pas dans le frontend.
- **Le frontend n'a pas de harnais de test** (ni vitest ni jest). La vérification de chaque tâche = `npx tsc -b` **et** `npx vite build` verts. La seule règle métier (inactivité) vit dans une **fonction pure** `estInactif(dernierMs, maintenantMs, limiteMs) -> boolean`, isolée pour être évidente et testable si un runner est ajouté un jour. **N'ajoute pas** de runner de test (hors périmètre).
- **Le login ne dépend jamais du fond** : une image absente laisse un fond uni, la connexion marche.
- **Copyright** : `© LSI-Maintenance {année}` avec `new Date().getFullYear()` (dynamique).
- **Inactivité** : constante `IDLE_MINUTES = 10`. Horodatage dans `localStorage` (clé `last_activity`) → survit au rechargement, vaut pour tous les onglets. Contrôle périodique (~30 s) **et** au `visibilitychange`. Repli en mémoire si `localStorage` inaccessible.
- **Version** : `__APP_VERSION__` (depuis `package.json`), `__BUILD_SHA__` (env `VITE_BUILD_SHA`, `"dev"` par défaut). « À propos » affiche les 7 premiers caractères du SHA.
- **« À propos »** : visible pour **tous** les utilisateurs (pas seulement admins), en **bas** de la barre latérale, ouvre un **modal** (pas une route).
- Commentaires et textes d'interface en français. Messages de commit en français **sans accents**.
- **Commande de vérification unique** (depuis `infra/`, Git Bash — `MSYS_NO_PATHCONV=1` INDISPENSABLE). On monte **tout** le frontend (pas seulement `src/`) pour que les changements hors `src/` — `vite.config.ts`, `package.json`, `vite-env.d.ts` — soient bien vus, et on installe les deps dans le conteneur pour ne dépendre d'aucun `node_modules` préexistant :

  ```bash
  MSYS_NO_PATHCONV=1 docker compose run --rm --no-deps -v "D:/code/dmarc/frontend:/app" -w /app frontend sh -c "npm install --silent && npx tsc -b && npx vite build"
  ```

  Dans les tâches ci-dessous, « lancer la vérification frontend » = cette commande. Attendu : `tsc` sans erreur, `vite build` réussi. **Ne pas** utiliser un montage `src/`-seul : il ne verrait pas `vite.config.ts` ni `package.json`.

## Structure des fichiers

| Fichier | Rôle |
|---|---|
| `frontend/src/assets/login-bg.jpg` | **Créer.** Fond du login, compressé (JPEG). |
| `frontend/src/pages/Login.tsx` | **Modifier.** Fond plein écran, carte semi-opaque, copyright. |
| `frontend/src/auth/idle.ts` | **Créer.** `estInactif(...)` (fonction pure) + `IDLE_MINUTES`. |
| `frontend/src/auth/useIdleLogout.ts` | **Créer.** Le hook (effets, écoute d'activité, déconnexion). |
| `frontend/src/components/Layout.tsx` | **Modifier.** Monte `useIdleLogout()` ; bouton « À propos » en bas ; état du modal. |
| `frontend/src/components/About.tsx` | **Créer.** Le modal « À propos ». |
| `frontend/vite.config.ts` | **Modifier.** `define` version + SHA. |
| `frontend/src/vite-env.d.ts` | **Modifier.** Globaux `__APP_VERSION__` / `__BUILD_SHA__`. |
| `frontend/package.json` | **Modifier.** Bump `version` → `0.2.0`. |
| `frontend/Dockerfile.prod` | **Modifier.** `ARG`/`ENV VITE_BUILD_SHA`. |
| `.github/workflows/build-images.yml` | **Modifier.** Passer `VITE_BUILD_SHA`. |

Ordre : 1 (fond+copyright) → 2 (inactivité) → 3 (version au build) → 4 (À propos). La tâche 4 consomme les globaux de la 3. Les tâches 2 et 4 touchent `Layout.tsx` mais des régions différentes (montage du hook vs bouton/modal), en séquence.

---

### Task 1: Fond d'écran du login + copyright

**Files:**
- Create: `frontend/src/assets/login-bg.jpg`
- Modify: `frontend/src/pages/Login.tsx`

**Interfaces:**
- Consumes: rien.
- Produces: rien (feuille).

- [ ] **Step 1: Fabriquer l'asset (copier + compresser)**

La source est sur l'hôte. La copier dans le dépôt, puis la convertir/compresser avec Pillow (conteneur backend) — une photo se compresse en JPEG, pas en PNG.

```bash
cd /d/code/dmarc
cp "/d/Philippe/Downloads/hf_20260715_111834_94230b08-d053-4947-8363-d6c558080910.png" frontend/src/assets/login-bg-raw.png
cd infra
MSYS_NO_PATHCONV=1 docker compose run --rm --no-deps -v "D:/code/dmarc:/repo" api python -c "
from PIL import Image
im = Image.open('/repo/frontend/src/assets/login-bg-raw.png').convert('RGB')
w,h = im.size
cible_h = 1200                               # assez pour un fond plein ecran, sans exces
if h > cible_h:
    im = im.resize((round(w*cible_h/h), cible_h), Image.LANCZOS)
im.save('/repo/frontend/src/assets/login-bg.jpg', 'JPEG', quality=82, optimize=True)
import os; print('login-bg.jpg :', im.size, os.path.getsize('/repo/frontend/src/assets/login-bg.jpg')//1024, 'Ko')
"
cd /d/code/dmarc && rm -f frontend/src/assets/login-bg-raw.png
```

Attendu : `login-bg.jpg : (…, 1200) …Ko` (viser < 300 Ko), et le fichier brut retiré.

- [ ] **Step 2: Modifier Login.tsx**

Remplacer le `return (...)` de `frontend/src/pages/Login.tsx` par (le reste du composant — état, `submit` — ne change pas ; garder l'import `logo` déjà présent, ajouter l'import du fond) :

En tête de fichier, à côté de `import logo from "../assets/logo-lsi.png";` :

```tsx
import loginBg from "../assets/login-bg.jpg";
```

Puis remplacer le bloc `return` :

```tsx
  return (
    <div
      className="min-h-screen flex flex-col items-center justify-center gap-4 bg-slate-800 bg-cover bg-center p-4"
      style={{ backgroundImage: `url(${loginBg})` }}
    >
      <form
        onSubmit={submit}
        className="w-80 space-y-4 rounded border bg-white/90 p-8 shadow-xl backdrop-blur"
      >
        <img src={logo} alt="LSI-Maintenance Mail Dispatch" className="mx-auto w-48 h-auto" />
        <h1 className="text-center text-lg font-semibold">Connexion</h1>
        <input className="border rounded w-full px-3 py-2" placeholder="Email"
               value={email} onChange={(e) => setEmail(e.target.value)} />
        <input className="border rounded w-full px-3 py-2" type="password" placeholder="Mot de passe"
               value={password} onChange={(e) => setPassword(e.target.value)} />
        {error && <p className="text-red-600 text-sm">{error}</p>}
        <button className="bg-blue-600 text-white rounded w-full py-2">Se connecter</button>
      </form>
      <p className="text-xs text-white/80">
        © LSI-Maintenance {new Date().getFullYear()}
      </p>
    </div>
  );
```

Le `bg-slate-800` est la couleur de repli sous l'image : si la photo ne charge pas, le fond reste sombre et la carte lisible.

- [ ] **Step 3: Lancer la vérification frontend** (voir Global Constraints)

Attendu : `tsc` sans erreur ; `vite build` réussi, `login-bg` bundlé dans `dist/assets/`.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/assets/login-bg.jpg frontend/src/pages/Login.tsx
git commit -m "feat(front): fond d ecran et copyright sur la page de login

Photo plein ecran (compressee en JPEG), boite de connexion sur fond blanc semi-opaque
avec leger flou pour rester lisible, copyright dynamique en dessous. Fond de repli sombre
si l image ne charge pas."
```

---

### Task 2: Déconnexion après 10 min d'inactivité

**Files:**
- Create: `frontend/src/auth/idle.ts`, `frontend/src/auth/useIdleLogout.ts`
- Modify: `frontend/src/components/Layout.tsx`

**Interfaces:**
- Consumes: `clearSession` (`../auth/session`), `useNavigate` (react-router).
- Produces:
  - `estInactif(dernierMs: number, maintenantMs: number, limiteMs: number): boolean`
  - `IDLE_MINUTES: number` (= 10), `IDLE_LIMIT_MS: number`
  - `useIdleLogout(): void` (hook, à monter dans la coquille authentifiée)

- [ ] **Step 1: La règle pure**

Créer `frontend/src/auth/idle.ts` :

```ts
// La seule regle metier de l'inactivite, isolee en fonction pure : evidente a lire, et
// testable telle quelle si un runner de test est ajoute un jour. Le reste (ecoute des
// evenements, minuteur, redirection) est de la plomberie de hook.
export const IDLE_MINUTES = 10;
export const IDLE_LIMIT_MS = IDLE_MINUTES * 60 * 1000;

/** Vrai si aucune activite depuis plus de `limiteMs`. */
export function estInactif(dernierMs: number, maintenantMs: number, limiteMs: number): boolean {
  return maintenantMs - dernierMs > limiteMs;
}
```

- [ ] **Step 2: Le hook**

Créer `frontend/src/auth/useIdleLogout.ts` :

```ts
import { useEffect } from "react";
import { useNavigate } from "react-router-dom";

import { IDLE_LIMIT_MS, estInactif } from "./idle";
import { clearSession } from "./session";

const CLE = "last_activity";
const CONTROLE_MS = 30_000;        // frequence du controle periodique

// Horodatage de derniere activite. En localStorage : survit au rechargement et vaut pour
// tous les onglets (une activite ici garde la session vivante la-bas). Repli en memoire si
// localStorage est inaccessible (navigation privee stricte) -- on ne casse jamais la
// session pour ca.
let memoire = Date.now();

function marquer(): void {
  memoire = Date.now();
  try {
    localStorage.setItem(CLE, String(memoire));
  } catch {
    /* localStorage indisponible : on garde la valeur en memoire */
  }
}

function dernier(): number {
  try {
    const v = localStorage.getItem(CLE);
    if (v) return Number(v);
  } catch {
    /* idem */
  }
  return memoire;
}

/** Deconnecte apres IDLE_MINUTES sans activite. A monter dans la coquille authentifiee. */
export function useIdleLogout(): void {
  const nav = useNavigate();

  useEffect(() => {
    marquer();      // on repart d'une activite fraiche a l'entree

    // Throttle : au plus une ecriture par seconde, sinon mousemove sature localStorage.
    let dernierMarquage = 0;
    const surActivite = () => {
      const t = Date.now();
      if (t - dernierMarquage > 1000) {
        dernierMarquage = t;
        marquer();
      }
    };

    const deconnecterSiInactif = () => {
      if (estInactif(dernier(), Date.now(), IDLE_LIMIT_MS)) {
        clearSession();
        nav("/login", { replace: true });
      }
    };

    const evenements = ["mousemove", "mousedown", "keydown", "scroll", "touchstart"] as const;
    evenements.forEach((e) => window.addEventListener(e, surActivite, { passive: true }));
    // Au retour sur l'onglet (reveil apres veille) : controle immediat, sans attendre le tic.
    document.addEventListener("visibilitychange", deconnecterSiInactif);
    const minuteur = window.setInterval(deconnecterSiInactif, CONTROLE_MS);

    return () => {
      evenements.forEach((e) => window.removeEventListener(e, surActivite));
      document.removeEventListener("visibilitychange", deconnecterSiInactif);
      window.clearInterval(minuteur);
    };
  }, [nav]);
}
```

- [ ] **Step 3: Monter le hook dans la coquille**

Dans `frontend/src/components/Layout.tsx`, ajouter l'import :

```tsx
import { useIdleLogout } from "../auth/useIdleLogout";
```

et l'appeler en tête du composant `Layout` (avant le `return`) :

```tsx
export function Layout() {
  useIdleLogout();
  const admin = isAdmin();
```

`Layout` étant rendu pour toutes les routes derrière `RequireAuth`, le minuteur ne tourne que sur les pages authentifiées — jamais sur `/login`.

- [ ] **Step 4: Lancer la vérification frontend** (voir Global Constraints)

Attendu : `tsc` et `vite build` verts.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/auth/idle.ts frontend/src/auth/useIdleLogout.ts frontend/src/components/Layout.tsx
git commit -m "feat(front): deconnexion apres 10 min d inactivite

La regle vit dans une fonction pure (estInactif) ; le hook ecoute l activite, marque un
horodatage dans localStorage (survit au rechargement, vaut pour tous les onglets, repli en
memoire), et deconnecte au controle periodique OU au retour sur l onglet apres veille. Le
token JWT garde son plafond absolu -- c est une couche d hygiene de session cote client."
```

---

### Task 3: Numéro de version injecté au build

**Files:**
- Modify: `frontend/vite.config.ts`, `frontend/src/vite-env.d.ts`, `frontend/package.json`, `frontend/Dockerfile.prod`, `.github/workflows/build-images.yml`

**Interfaces:**
- Produces: les globaux `__APP_VERSION__: string` et `__BUILD_SHA__: string`, disponibles dans tout le code frontend (consommés par la tâche 4).

- [ ] **Step 1: Injecter version + SHA dans vite.config**

Remplacer `frontend/vite.config.ts` par :

```ts
import { readFileSync } from "node:fs";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Version lue depuis package.json par le systeme de fichiers (pas un import JSON, qui
// exigerait resolveJsonModule dans le tsconfig du build). Le SHA vient de la CI ; en local,
// "dev" -- on ne pretend pas connaitre un commit qu'on n'a pas.
const pkg = JSON.parse(
  readFileSync(new URL("./package.json", import.meta.url), "utf-8"),
) as { version: string };

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, host: true },
  define: {
    __APP_VERSION__: JSON.stringify(pkg.version),
    __BUILD_SHA__: JSON.stringify(process.env.VITE_BUILD_SHA || "dev"),
  },
});
```

- [ ] **Step 2: Déclarer les globaux pour TypeScript**

Ajouter à la fin de `frontend/src/vite-env.d.ts` :

```ts
declare const __APP_VERSION__: string;
declare const __BUILD_SHA__: string;
```

- [ ] **Step 3: Bump la version**

Dans `frontend/package.json`, passer `"version": "0.1.0"` à `"version": "0.2.0"`.

- [ ] **Step 4: Câbler le SHA dans le build Docker**

Dans `frontend/Dockerfile.prod`, ajouter l'`ARG`/`ENV` **avant** `RUN npm run build`, à côté de `VITE_API_URL` :

```dockerfile
ARG VITE_API_URL
ENV VITE_API_URL=$VITE_API_URL
ARG VITE_BUILD_SHA
ENV VITE_BUILD_SHA=$VITE_BUILD_SHA
RUN npm run build
```

- [ ] **Step 5: Passer le SHA depuis la CI**

Dans `.github/workflows/build-images.yml`, à l'étape `docker/build-push-action@v6`, remplacer `build-args: ${{ matrix.build_args }}` par :

```yaml
          build-args: |
            ${{ matrix.build_args }}
            VITE_BUILD_SHA=${{ github.sha }}
```

`github.sha` est le SHA complet du commit ; « À propos » n'en affichera que les 7 premiers caractères. L'image `report-api` recevra aussi cet argument sans le consommer (avertissement Docker inoffensif) — c'est le prix d'un seul point de passage des `build-args` pour les deux images.

- [ ] **Step 6: Lancer la vérification frontend** (voir Global Constraints)

Cette commande monte tout le frontend : elle voit donc le nouveau `vite.config.ts`, le `package.json` bumpé et `vite-env.d.ts`. Attendu : `tsc` sans erreur (globaux `__APP_VERSION__`/`__BUILD_SHA__` typés), `vite build` réussi (les globaux sont remplacés à la compilation → `pkg.version` lu, `VITE_BUILD_SHA` absent en local ⇒ `"dev"`).

Confirmer que la version est bien injectée dans le bundle :
```bash
MSYS_NO_PATHCONV=1 docker compose run --rm --no-deps -v "D:/code/dmarc/frontend:/app" -w /app frontend sh -c "npm install --silent && npx vite build && grep -rl '0.2.0' dist/assets/*.js >/dev/null && echo 'version 0.2.0 presente dans le bundle'"
```
Attendu : `version 0.2.0 presente dans le bundle`.

- [ ] **Step 7: Commit**

```bash
git add frontend/vite.config.ts frontend/src/vite-env.d.ts frontend/package.json frontend/Dockerfile.prod .github/workflows/build-images.yml
git commit -m "feat(front): version injectee au build (package.json + SHA du commit)

vite.config expose __APP_VERSION__ (lu dans package.json) et __BUILD_SHA__ (env
VITE_BUILD_SHA, defaut dev). Le SHA est passe par la CI au build de l image frontend.
Version bumpee a 0.2.0."
```

---

### Task 4: Boîte « À propos » + entrée dans la barre latérale

**Files:**
- Create: `frontend/src/components/About.tsx`
- Modify: `frontend/src/components/Layout.tsx`

**Interfaces:**
- Consumes: `__APP_VERSION__` / `__BUILD_SHA__` (Task 3), `logo` (`../assets/logo-lsi.png`).
- Produces: rien (feuille).

- [ ] **Step 1: Le modal**

Créer `frontend/src/components/About.tsx` (même patron que `PasswordDialog` : fond cliquable qui ferme, contenu qui stoppe la propagation) :

```tsx
import logo from "../assets/logo-lsi.png";

/** Boite « A propos » : nom, version, SHA du build, copyright. Ouverte depuis la barre
 *  laterale. Le SHA est tronque a 7 caracteres (comme un `git log --oneline`). */
export function About({ onClose }: { onClose: () => void }) {
  return (
    <div
      className="fixed inset-0 z-30 flex items-center justify-center bg-black/30 p-4"
      onMouseDown={onClose}
    >
      <div
        className="w-full max-w-sm rounded border bg-white p-6 text-center"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <img src={logo} alt="LSI-Maintenance Mail Dispatch" className="mx-auto w-40 h-auto" />
        <dl className="mt-4 space-y-1 text-sm">
          <div className="flex justify-between">
            <dt className="text-gray-500">Version</dt>
            <dd className="font-mono">{__APP_VERSION__}</dd>
          </div>
          <div className="flex justify-between">
            <dt className="text-gray-500">Build</dt>
            <dd className="font-mono">{__BUILD_SHA__.slice(0, 7)}</dd>
          </div>
        </dl>
        <p className="mt-4 text-xs text-gray-500">© LSI-Maintenance {new Date().getFullYear()}</p>
        <button
          onClick={onClose}
          className="mt-4 w-full rounded border py-2 text-sm"
        >
          Fermer
        </button>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: L'entrée en bas de la barre latérale**

Dans `frontend/src/components/Layout.tsx` :

Ajouter les imports :

```tsx
import { useState } from "react";

import { About } from "./About";
```

(`useState` : vérifier qu'il n'est pas déjà importé ; sinon compléter l'import existant de `react`.)

Ajouter l'état dans le composant :

```tsx
  const [apropos, setApropos] = useState(false);
```

Faire de la `<nav>` une colonne flex et pousser « À propos » en bas. Remplacer la balise
ouvrante `<nav className="w-56 shrink-0 border-r bg-white p-4">` par :

```tsx
      <nav className="flex w-56 shrink-0 flex-col border-r bg-white p-4">
```

et, **juste avant** la fermeture `</nav>`, ajouter (après le `</div>` des liens de navigation) :

```tsx
        <button
          onClick={() => setApropos(true)}
          className="mt-auto pt-4 text-left text-sm text-gray-500 hover:text-gray-900"
        >
          À propos
        </button>
```

Le `mt-auto` pousse le bouton tout en bas. Il est **hors** du bloc `admin &&` : visible pour tous.

Enfin, rendre le modal. Juste avant la fermeture du `return` de `Layout` (après le dernier `</div>` de la coquille, à l'intérieur du fragment/div racine), ajouter :

```tsx
      {apropos && <About onClose={() => setApropos(false)} />}
```

(si le `return` racine est un unique `<div>`, placer cette ligne juste avant sa balise fermante `</div>`.)

- [ ] **Step 3: Lancer la vérification frontend** (voir Global Constraints)

Attendu : `tsc` et `vite build` verts. (`__APP_VERSION__`/`__BUILD_SHA__` sont définis par le `vite.config` de la tâche 3 — le build les remplace.)

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/About.tsx frontend/src/components/Layout.tsx
git commit -m "feat(front): boite A propos (version + build) depuis la barre laterale

Une entree A propos en bas de la barre, visible pour tous, ouvre un modal affichant le
logo, la version, le SHA court du build et le copyright."
```

---

## Vérification finale

- [ ] La vérification frontend (voir Global Constraints) — `tsc` + `vite build` verts.
- [ ] `cd infra && docker compose build frontend` — l'image se construit (prouve la chaîne de build réelle).
- [ ] **Contrôle réel dans le navigateur** (le build ne prouve pas le rendu ni le comportement) :
  - Login : la photo de fond s'affiche, la boîte est lisible par-dessus, le copyright en dessous.
  - Inactivité : baisser temporairement `IDLE_MINUTES` (ex. `0.2`) dans `idle.ts`, se connecter, ne rien toucher → déconnexion vers `/login` ; bouger la souris repousse l'échéance ; remettre `10`.
  - À propos : le bouton en bas de la barre ouvre le modal ; version `0.2.0`, build `dev` en local (le vrai SHA après un build CI) ; le clic hors du modal le ferme.

## Ce que ce plan ne fait PAS, délibérément

- **Pas de raccourcissement du token JWT ni de refresh token.** Le minuteur front couvre le poste laissé ouvert ; le refresh serait un chantier serveur non demandé.
- **Pas de harnais de test frontend.** Hors périmètre ; la règle d'inactivité reste en fonction pure pour rester testable plus tard.
- **« À propos » minimal** : nom, version, build, copyright. Pas de changelog.
