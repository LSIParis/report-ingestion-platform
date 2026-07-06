# Plateforme d'ingestion de rapports par e-mail (multitenant)

Ingestion automatisée de rapports envoyés par différentes marques dans une boîte mail
partagée → parsing (corps/CSV/XLSX/PDF) → normalisation → PostgreSQL (RLS) → dashboard
isolé par tenant.

- Spécification normative : [`SPEC.md`](./SPEC.md)
- Règles permanentes pour l'agent de code : [`CLAUDE.md`](./CLAUDE.md)

## Démarrage rapide

```bash
# 1. Clés JWT (une fois)
openssl genrsa -out keys/jwt_priv.pem 2048
openssl rsa -in keys/jwt_priv.pem -pubout -out keys/jwt_pub.pem

# 2. Environnement
cp infra/.env.example infra/.env    # renseigne IMAP_* + charge les clés

# 3. Lancer la stack (migrate applique 0001_schema puis 0002_rls)
cd infra && docker compose up --build -d

# 4. Seed (2 tenants, 3 users)
docker compose exec api python -m scripts.seed

# 5. Login → JWT
curl -s localhost:8000/auth/login -H 'Content-Type: application/json' \
  -d '{"email":"user@acme.com","password":"acme"}'
```

- API : http://localhost:8000/docs
- Front : http://localhost:5173
- MinIO : http://localhost:9001

## Structure

```
backend/   FastAPI + Celery + parsing (voir SPEC.md §8.1)
frontend/  React + TypeScript (dashboard)
infra/     docker-compose + rôles RLS
```

## Tests

```bash
docker compose exec api pytest                              # tout
docker compose exec api pytest tests/test_tenant_isolation.py -v   # isolation (bloquant CI)
```
