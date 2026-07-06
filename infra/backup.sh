#!/usr/bin/env bash
# Sauvegarde PostgreSQL + MinIO. À placer dans un cron quotidien.
# Usage : ./backup.sh   (depuis infra/, avec .env.prod présent)
set -euo pipefail

cd "$(dirname "$0")"
COMPOSE="docker compose -f docker-compose.prod.yml --env-file .env.prod"
NETWORK="infra_default"                     # réseau par défaut du compose (dossier = infra/)
STAMP="$(date +%Y%m%d-%H%M%S)"
DEST="${BACKUP_DIR:-$PWD/backups}/$STAMP"
mkdir -p "$DEST/minio"

# shellcheck disable=SC1091
set -a; source .env.prod; set +a

echo "[backup] PostgreSQL → $DEST/db.sql.gz"
$COMPOSE exec -T postgres pg_dump -U postgres reports | gzip > "$DEST/db.sql.gz"

echo "[backup] MinIO (reports-raw) → $DEST/minio/"
docker run --rm --network "$NETWORK" -v "$DEST/minio:/backup" minio/mc sh -c "
  mc alias set local http://minio:9000 '$MINIO_ROOT_USER' '$MINIO_ROOT_PASSWORD' >/dev/null &&
  mc mirror --overwrite local/reports-raw /backup"

# Rétention : garder 14 jours
find "${BACKUP_DIR:-$PWD/backups}" -maxdepth 1 -type d -mtime +14 -exec rm -rf {} + 2>/dev/null || true
echo "[backup] terminé : $DEST"
