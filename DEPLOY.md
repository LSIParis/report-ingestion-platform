# Déploiement production sur VPS

Guide pas à pas pour mettre la plateforme en production sur un VPS (Ubuntu 22.04/24.04),
en Docker Compose, avec TLS automatique via Caddy. Base et object store restent **privés**
(seul Caddy publie 80/443).

## Architecture déployée

```
Internet
  │  :80 / :443 (TLS Let's Encrypt auto)
  ▼
┌─────────┐   rapports.domaine.tld ──► frontend (React, nginx)
│  Caddy  │   rapports.domaine.tld/api/* ──► api (FastAPI, uvicorn x4)
│         │   files.rapports.domaine.tld ──► minio (URLs signées)
└─────────┘
  (réseau docker interne, non exposé)
     ├── api / worker / imap-worker (FastAPI + Celery)
     ├── postgres (RLS)         [pas de port public]
     ├── minio (object store)   [via Caddy uniquement]
     └── redis (broker)         [pas de port public]
```

## 0. Prérequis

- Un VPS avec IP publique fixe, 2 vCPU / 4 Go RAM minimum (le parsing PDF/OCR est gourmand).
- Un nom de domaine et l'accès à sa zone DNS.
- La boîte mail machine IMAP (`reports@lsiparis.tech`) et ses identifiants.

## 1. DNS

Créer **deux** enregistrements A (et AAAA si IPv6) vers l'IP du VPS :

| Type | Nom | Valeur |
|------|-----|--------|
| A | `rapports` | `<IP_DU_VPS>` |
| A | `files.rapports` | `<IP_DU_VPS>` |

Le sous-domaine `files.*` sert aux URLs signées de téléchargement des fichiers sources.

## 2. Préparer le VPS

```bash
# Connexion
ssh root@<IP_DU_VPS>

# Docker + compose plugin
curl -fsSL https://get.docker.com | sh

# Pare-feu : n'ouvrir que SSH + HTTP + HTTPS
ufw allow 22/tcp && ufw allow 80/tcp && ufw allow 443/tcp && ufw --force enable

# Utilisateur non-root (optionnel mais recommandé)
adduser deploy && usermod -aG docker deploy && su - deploy
```

## 3. Récupérer le code

```bash
git clone https://github.com/LSIParis/report-ingestion-platform.git
cd report-ingestion-platform
```

## 4. Générer les clés JWT

```bash
mkdir -p keys
openssl genrsa -out keys/jwt_priv.pem 2048
openssl rsa -in keys/jwt_priv.pem -pubout -out keys/jwt_pub.pem
chmod 600 keys/jwt_priv.pem
# keys/ est déjà gitignoré : les clés ne partent jamais sur GitHub.
```

## 5. Configurer les secrets

```bash
cd infra
cp .env.prod.example .env.prod
nano .env.prod         # renseigner DOMAIN, FILES_DOMAIN, ACME_EMAIL, IMAP_*,
                       # et générer des mots de passe forts :
                       #   openssl rand -base64 24
chmod 600 .env.prod
```

Champs à remplir : `DOMAIN`, `FILES_DOMAIN`, `ACME_EMAIL`, `POSTGRES_PASSWORD`,
`MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD`, `IMAP_HOST`, `IMAP_USER`, `IMAP_PASSWORD`.

## 6. Lancer

```bash
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build
```

Ordre garanti : `postgres` → `migrate` (schéma + **RLS**) → `api`/`worker`/`imap-worker` →
`frontend` → `caddy` (qui obtient les certificats TLS). Le premier démarrage prend quelques
minutes (build + certificats).

Vérifier :
```bash
docker compose -f docker-compose.prod.yml ps          # tous "running"/"healthy"
docker compose -f docker-compose.prod.yml logs -f caddy   # doit montrer les certificats obtenus
curl -sI https://rapports.mondomaine.tld/api/../health   # ou GET /api/... selon routing
```

## 7. Amorcer les données

Créer les tenants, utilisateurs et règles de matching. Adapter `scripts/seed.py`
(ou insérer via l'API admin) à tes vrais domaines. Pour un premier test :

```bash
docker compose -f docker-compose.prod.yml --env-file .env.prod exec api python -m scripts.seed
```

⚠️ **Change les mots de passe du seed** (`admin`, `acme`, `globex`) avant toute mise en
service réelle — ce sont des valeurs de démonstration.

## 8. Vérifier le flux complet

1. Ouvrir `https://rapports.mondomaine.tld` → écran de connexion.
2. Se connecter avec un compte tenant.
3. Envoyer un mail de test à la boîte IMAP depuis une adresse mappée (règle `sender`).
4. Sous ~45 s (polling IMAP), le rapport apparaît dans le dashboard — **et uniquement**
   pour son tenant.

## 9. Sauvegardes

```bash
chmod +x backup.sh
./backup.sh                      # test manuel (PostgreSQL + MinIO)

# Cron quotidien à 3h
crontab -e
# 0 3 * * * cd /home/deploy/report-ingestion-platform/infra && ./backup.sh >> backup.log 2>&1
```

Restauration DB : `gunzip -c db.sql.gz | docker compose -f docker-compose.prod.yml exec -T postgres psql -U postgres reports`.

## 10. Mises à jour

```bash
cd report-ingestion-platform
git pull
cd infra
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build
# `migrate` applique automatiquement les nouvelles migrations Alembic.
```

## Sécurité — check-list prod

- [ ] `.env.prod` et `keys/` en `chmod 600`, jamais committés (déjà gitignorés).
- [ ] Mots de passe DB/MinIO forts et uniques (`openssl rand`).
- [ ] Comptes du seed remplacés par de vrais comptes.
- [ ] Pare-feu : seuls 22/80/443 ouverts ; base et redis non exposés (aucun `ports:` public).
- [x] Antivirus des pièces jointes (ClamAV) : service `clamav` inclus, scan actif en prod
      (`ANTIVIRUS_ENABLED=true`). 1er démarrage ~2-3 min (chargement des signatures).
- [ ] Sauvegardes testées (restauration vérifiée, pas seulement le dump).
- [ ] `ACME_EMAIL` valide (notifications d'expiration de certificat).
- [ ] Isolation multitenant : le test `pytest tests/test_tenant_isolation.py` passe.

## Dépannage

| Symptôme | Piste |
|---|---|
| Caddy n'obtient pas le certificat | DNS pas encore propagé, ou 80/443 fermés par le pare-feu / hébergeur. |
| Le téléchargement du fichier source échoue (403) | `FILES_DOMAIN` mal résolu, ou `S3_PUBLIC_ENDPOINT` ≠ `https://FILES_DOMAIN`. |
| Aucun mail ingéré | Vérifier `IMAP_*` et les logs `imap-worker`. La boîte doit être en IMAP, pas POP3. |
| `needs_review` systématique | Aucune règle `sender`/`subject_regex` ne matche → ajouter des règles via l'admin. |
