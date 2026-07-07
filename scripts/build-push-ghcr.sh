#!/usr/bin/env bash
# ============================================================================
# Build + push des images vers GHCR — équivalent local du workflow
# .github/workflows/build-images.yml (utile tant que les runners Actions
# ne sont pas disponibles).
#
# Produit :
#   ghcr.io/<owner>/report-api        (backend)
#   ghcr.io/<owner>/report-frontend   (frontend, VITE_API_URL=/api)
# taggées avec :latest ET :sha-<court>.
#
# Usage :
#   export CR_PAT=<PAT avec scope write:packages>      # requis pour le login
#   export GHCR_USER=LSIParis                          # ton compte GitHub
#   ./scripts/build-push-ghcr.sh [tag_supplementaire]
#
# Variables optionnelles :
#   OWNER      (défaut: lsiparis)   — namespace GHCR, DOIT être en minuscules
#   REGISTRY   (défaut: ghcr.io)
#   NO_PUSH=1                       — build seulement, sans push
# ============================================================================
set -euo pipefail

REGISTRY="${REGISTRY:-ghcr.io}"
OWNER="${OWNER:-lsiparis}"                 # GHCR exige un namespace minuscule
API_IMAGE="$REGISTRY/$OWNER/report-api"
FRONTEND_IMAGE="$REGISTRY/$OWNER/report-frontend"

# Racine du repo = dossier parent de scripts/
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

log() { printf '\033[1;34m[ghcr]\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m[ghcr] ERREUR:\033[0m %s\n' "$*" >&2; exit 1; }

# ---- Préflight ----
command -v docker >/dev/null || die "docker introuvable."
docker info >/dev/null 2>&1 || die "le démon Docker ne répond pas."
[ -f backend/Dockerfile ] || die "backend/Dockerfile absent (mauvais répertoire ?)."
[ -f frontend/Dockerfile.prod ] || die "frontend/Dockerfile.prod absent."

# Tag court basé sur le commit (fallback 'manual' hors git)
if SHA="$(git rev-parse --short HEAD 2>/dev/null)"; then
  SHA_TAG="sha-$SHA"
else
  SHA_TAG="manual"
fi
EXTRA_TAG="${1:-}"                          # tag supplémentaire optionnel (ex: v1.0.0)

# ---- Login (sauf NO_PUSH) ----
if [ "${NO_PUSH:-0}" != "1" ]; then
  [ -n "${CR_PAT:-}" ] || die "CR_PAT non défini (PAT avec scope write:packages)."
  [ -n "${GHCR_USER:-}" ] || die "GHCR_USER non défini (ton login GitHub)."
  log "Login sur $REGISTRY en tant que $GHCR_USER"
  echo "$CR_PAT" | docker login "$REGISTRY" -u "$GHCR_USER" --password-stdin \
    || die "échec du login GHCR."
fi

build_push() {
  local image="$1"; shift
  local context="$1"; shift
  local dockerfile="$1"; shift
  # arguments restants = --build-arg ...
  local tags=(-t "$image:latest" -t "$image:$SHA_TAG")
  [ -n "$EXTRA_TAG" ] && tags+=(-t "$image:$EXTRA_TAG")

  log "Build $image ($SHA_TAG${EXTRA_TAG:+, $EXTRA_TAG})"
  docker build "${tags[@]}" -f "$dockerfile" "$@" "$context"

  if [ "${NO_PUSH:-0}" != "1" ]; then
    log "Push $image (toutes les tags)"
    docker push --all-tags "$image"
  else
    log "NO_PUSH=1 → push ignoré pour $image"
  fi
}

build_push "$API_IMAGE"      backend  backend/Dockerfile
build_push "$FRONTEND_IMAGE" frontend frontend/Dockerfile.prod --build-arg VITE_API_URL=/api

log "Terminé."
log "Images :"
log "  $API_IMAGE:latest ($SHA_TAG)"
log "  $FRONTEND_IMAGE:latest ($SHA_TAG)"
if [ "${NO_PUSH:-0}" != "1" ]; then
  cat <<EOF

  → Rends les 2 packages PUBLICS pour que Portainer les tire sans identifiants :
    GitHub → Packages → report-api / report-frontend → Package settings
           → Change visibility → Public
    (ou ajoute un registre GHCR dans Portainer avec un PAT read:packages)

  → Déploie ensuite via infra/portainer-stack.ghcr.yml
EOF
fi
