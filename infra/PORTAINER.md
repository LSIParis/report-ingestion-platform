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

## Méthode B — Web editor (coller le compose)

Utile sans dépôt connecté. Portainer ne peut pas construire depuis un contexte local en
mode Web editor : il faut donc des **images pré-construites** poussées sur un registre
(ex. GHCR). Étapes :

1. Construire et pousser les images une fois (depuis ta machine ou un runner) :
   ```bash
   docker build -t ghcr.io/lsiparis/report-api:latest ./backend
   docker build -f ./frontend/Dockerfile.prod --build-arg VITE_API_URL=/api \
     -t ghcr.io/lsiparis/report-frontend:latest ./frontend
   docker push ghcr.io/lsiparis/report-api:latest
   docker push ghcr.io/lsiparis/report-frontend:latest
   ```
2. Dans `portainer-stack.yml`, remplacer les blocs `image: report-platform-*:portainer` +
   `build:` par les images GHCR ci-dessus (retirer les `build:`), et remplacer les
   bind-mounts `./Caddyfile` / `./init-roles.sql` par des `configs:` inline (Portainer récent)
   ou une image Caddy/Postgres personnalisée embarquant ces fichiers.
3. Coller le compose modifié dans **Stacks → Add stack → Web editor**, définir les mêmes
   variables, **Deploy**.

> La méthode A évite tout ce travail : préfère-la sauf contrainte spécifique.

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
