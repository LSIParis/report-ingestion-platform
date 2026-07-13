# Déploiement via Portainer

Deux méthodes. La **méthode Repository (A)** est recommandée : Portainer clone le dépôt,
construit les images et trouve les fichiers `Caddyfile` / `init-roles.sql` — rien à préparer
à la main. La méthode **Web editor (B)** convient si tu ne veux pas connecter de dépôt.

---

## Prérequis (communs)

1. **DNS** : deux enregistrements A vers l'IP de l'hôte Portainer
   - `rapports.mondomaine.tld`
   - `files.rapports.mondomaine.tld`
2. **Ports 80 et 443** ouverts sur l'hôte (et non déjà pris par un autre reverse-proxy —
   si Portainer tourne déjà derrière un proxy, voir la note « proxy existant » en bas).
3. **Clés JWT** générées en local (une seule fois) :
   ```bash
   openssl genrsa -out jwt_priv.pem 2048
   openssl rsa -in jwt_priv.pem -pubout -out jwt_pub.pem
   ```
   Tu colleras leur contenu dans les variables `JWT_PRIVATE_KEY` / `JWT_PUBLIC_KEY`.

---

## Méthode A — Repository (recommandée)

1. Portainer → **Stacks** → **Add stack** → onglet **Repository**.
2. Renseigner :
   - **Repository URL** : `https://github.com/LSIParis/report-ingestion-platform`
   - **Repository reference** : `refs/heads/main`
   - **Compose path** : `infra/portainer-stack.yml`
3. Section **Environment variables** → ajouter les variables du tableau ci-dessous
   (bouton *Add an environment variable*, ou *Advanced mode* pour coller en bloc).
4. **Deploy the stack**. Le premier déploiement construit les images (quelques minutes),
   applique les migrations (schéma + RLS), puis Caddy obtient les certificats TLS.

### Variables d'environnement à définir

| Variable | Exemple / note |
|---|---|
| `DOMAIN` | `rapports.mondomaine.tld` |
| `FILES_DOMAIN` | `files.rapports.mondomaine.tld` |
| `ACME_EMAIL` | `admin@mondomaine.tld` (notifications de certificat) |
| `POSTGRES_PASSWORD` | valeur forte (`openssl rand -base64 24`) |
| `MINIO_ROOT_USER` | ex. `reportminio` |
| `MINIO_ROOT_PASSWORD` | valeur forte |
| `IMAP_HOST` | `imap.mondomaine.tld` |
| `IMAP_USER` | `reports@lsiparis.tech` |
| `IMAP_PASSWORD` | mot de passe de la boîte |
| `JWT_PRIVATE_KEY` | **coller tout le contenu** de `jwt_priv.pem` (multilignes, `-----BEGIN...`) |
| `JWT_PUBLIC_KEY` | **coller tout le contenu** de `jwt_pub.pem` |
| `SENTRY_DSN` | (optionnel) |

> Les champs multilignes (`JWT_*`) : dans Portainer, colle le PEM complet tel quel dans la
> valeur de la variable — les retours à la ligne sont conservés.

---

## Méthode B — Images pré-construites (GHCR), sans build sur l'hôte

Identique à la méthode A (mode **Repository**) mais Portainer **tire** les images au lieu de
les construire → déploiement plus rapide et hôte moins sollicité.

### 1. Publier les images (automatique)

Le workflow `.github/workflows/build-images.yml` construit et pousse à chaque `push` sur
`main` :
- `ghcr.io/lsiparis/report-api` (backend)
- `ghcr.io/lsiparis/report-frontend` (frontend, build-arg `VITE_API_URL=/api`)

Tags produits : `latest` (sur `main`), `sha-<court>`, et `vX.Y.Z` sur les tags Git.

> Build manuel possible si besoin (runners indisponibles) :
> ```bash
> echo $CR_PAT | docker login ghcr.io -u <user> --password-stdin
> docker build -t ghcr.io/lsiparis/report-api:latest ./backend && docker push ghcr.io/lsiparis/report-api:latest
> docker build -f ./frontend/Dockerfile.prod --build-arg VITE_API_URL=/api \
>   -t ghcr.io/lsiparis/report-frontend:latest ./frontend && docker push ghcr.io/lsiparis/report-frontend:latest
> ```

### 2. Rendre les images accessibles

Après le premier push, les packages GHCR sont **privés**. Deux options :
- **Public** (simple) : GitHub → *Packages* → chaque image → *Package settings* →
  *Change visibility* → **Public**. Portainer tire sans identifiants.
- **Privé** : Portainer → *Registries* → *Add registry* → *Custom* (`ghcr.io`) avec un
  PAT `read:packages`. La stack pourra alors tirer les images privées.

### 3. Déployer

Comme la méthode A, mais **Compose path** : `infra/portainer-stack.ghcr.yml`
(mêmes variables d'environnement). Portainer clone le repo (pour `Caddyfile` /
`init-roles.sql`) et tire les images GHCR.

> Le « pur » Web editor (coller le compose sans dépôt) n'est pas recommandé ici :
> les fichiers `Caddyfile` et `init-roles.sql` devraient être inlinés, ce que
> l'interpolation Compose complique (`{$DOMAIN}` de Caddy, `$$` du SQL). Le mode
> Repository + images GHCR donne le même résultat sans ces pièges.

---

## Méthode C — Derrière un reverse-proxy existant (Nginx Proxy Manager)

À utiliser quand l'hôte a **déjà** un proxy sur 80/443 (NPM, Traefik…). C'est le cas de
la prod actuelle. Compose path : `infra/portainer-stack.npm.yml`.

La stack ne publie **aucun port**. Seuls `frontend` et `minio` rejoignent le réseau
externe du proxy (par défaut `proxy`), sous les alias `reports-frontend` et
`reports-minio`. Postgres, Redis, ClamAV et les workers restent sur un réseau privé.

### Deux pièges spécifiques aux endpoints **agent** (hôte Docker distant)

1. **Aucun bind-mount de fichier du dépôt ne fonctionne.** Portainer clone le dépôt sur
   *son* serveur, pas sur l'hôte cible : Docker crée un répertoire vide à la place du
   fichier. C'est pourquoi les rôles DB sont créés par `scripts/ensure_roles.py` depuis
   le conteneur `migrate`, et non par un `init-roles.sql` monté.
2. **Aucune variable ne peut être multiligne.** Portainer les écrit dans un `stack.env`
   (une ligne par variable) : un PEM y casse le parsing. D'où `JWT_*_KEY_B64`.

### Les deux Proxy Hosts à créer dans NPM

| | Application | Fichiers (URLs signées) |
|---|---|---|
| **Domain Names** | `DOMAIN` | `FILES_DOMAIN` |
| **Scheme** | `http` | `http` |
| **Forward Hostname** | `reports-frontend` | `reports-minio` |
| **Forward Port** | `80` | `9000` |
| **SSL** | Request a new certificate + Force SSL | idem |

Le nginx du frontend route lui-même `/api/*` vers l'API (préfixe retiré) : NPM n'a qu'un
seul upstream par domaine, aucun routage par chemin à configurer.

> ⚠️ **Ne pas activer le proxy Cloudflare (nuage orange)** sur `FILES_DOMAIN` : MinIO
> signe ses URLs avec le *Host*, et toute réécriture invalide la signature SigV4. NPM,
> lui, conserve le Host par défaut.

### Provisionnement

```bash
# Console du conteneur api (Portainer → conteneur api → Console → /bin/sh)
python -m scripts.add_tenant exemple.com "Exemple SA"     # + règle subject_regex DMARC
USER_PASSWORD=... python -m scripts.add_user a@b.tld tenant_viewer exemple.com
python -m scripts.requeue needs_review                    # rejoue la quarantaine
python -m scripts.reingest needs_review                   # re-lit les .eml depuis S3
```

---

## Après déploiement

- Amorcer les données : Portainer → conteneur `api` → **Console** (`/bin/sh`) →
  `python -m scripts.seed` (⚠️ change les mots de passe de démo ensuite).
- Vérifier : ouvrir `https://rapports.mondomaine.tld` → écran de connexion.
- Envoyer un mail de test depuis une adresse mappée → il apparaît dans le dashboard du
  bon tenant sous ~45 s.
- Sauvegardes : voir `infra/backup.sh` et `DEPLOY.md` §9.

## Mises à jour

Portainer → Stacks → la stack → **Pull and redeploy** (mode Repository) : Portainer récupère
le dernier `main`, reconstruit, et `migrate` applique les nouvelles migrations Alembic.

## Note « proxy existant »

Si Portainer (ou un autre service) occupe déjà 80/443 via un reverse-proxy (Traefik, NPM…),
retire le service `caddy` de la stack, n'expose pas 80/443, et branche ton proxy existant sur :
- `frontend:80` pour le domaine principal, avec `/api/*` → `api:8000` (préfixe retiré),
- `minio:9000` pour `FILES_DOMAIN`, **en conservant le Host** (sinon les URLs signées cassent).
