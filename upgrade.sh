#!/usr/bin/env bash
set -euo pipefail

# ── Self-Hosted Library upgrade ─────────────────────────────────────────────
# Safely updates to the latest images, runs migrations, and verifies health.
# Rolls back on failure.
#
# Usage:
#   ./upgrade.sh              # pull latest, migrate, health check
#   ./upgrade.sh --dry-run     # print what would happen
#   ./upgrade.sh v0.2.0        # upgrade to a specific version tag
# ────────────────────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

VERSION="${1:-latest}"
DRY_RUN=false
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=true ;;
    esac
done

say()   { echo -e "${CYAN}→${NC} $*"; }
ok()    { echo -e "  ${GREEN}✓${NC} $*"; }
warn()  { echo -e "  ${YELLOW}⚠${NC} $*"; }
fail()  { echo -e "  ${RED}✗${NC} $*"; }

# ── preflight ───────────────────────────────────────────────────────────────

if ! docker compose version >/dev/null 2>&1; then
    fail "docker compose not found"
    exit 1
fi

if [ ! -f .env ]; then
    fail ".env not found — run ./setup.sh first"
    exit 1
fi

# ── detect compose files ────────────────────────────────────────────────────

COMPOSE_FILES=(-f compose.yaml -f compose.images.yaml)
if [ -f compose.gpu.yaml ]; then
    if docker compose -f compose.yaml -f compose.gpu.yaml config --quiet 2>/dev/null; then
        COMPOSE_FILES+=(-f compose.gpu.yaml)
    fi
fi

# ── backup .env ─────────────────────────────────────────────────────────────

say "Backing up .env..."
BACKUP=".env.backup-$(date -u +%Y%m%dT%H%M%SZ)"
if $DRY_RUN; then
    echo "  [dry-run] Would copy .env → ${BACKUP}"
else
    cp .env "$BACKUP"
    ok "Backed up to ${BACKUP}"
fi

# ── pull new images ─────────────────────────────────────────────────────────

say "Pulling images (version: ${VERSION})..."
if $DRY_RUN; then
    echo "  [dry-run] Would pull app + worker images"
else
    docker compose "${COMPOSE_FILES[@]}" pull app worker 2>&1 | while IFS= read -r line; do
        echo "  $line"
    done
    ok "Images pulled"
fi

# ── run migrations ──────────────────────────────────────────────────────────

say "Running database migrations..."
if $DRY_RUN; then
    echo "  [dry-run] Would run: docker compose run --rm app alembic upgrade head"
else
    if docker compose "${COMPOSE_FILES[@]}" run --rm -T app alembic upgrade head 2>&1; then
        ok "Migrations complete"
    else
        fail "Migration failed — rolling back"
        docker compose "${COMPOSE_FILES[@]}" down app worker 2>/dev/null || true
        docker compose "${COMPOSE_FILES[@]}" up -d --wait app worker 2>&1 | tail -3
        fail "Upgrade aborted. Previous version restored."
        exit 1
    fi
fi

# ── restart services ────────────────────────────────────────────────────────

say "Restarting services..."
if $DRY_RUN; then
    echo "  [dry-run] Would restart app + worker"
else
    docker compose "${COMPOSE_FILES[@]}" up -d --wait app worker 2>&1 | while IFS= read -r line; do
        echo "  $line"
    done
    ok "Services restarted"
fi

# ── health check ────────────────────────────────────────────────────────────

say "Verifying health..."
if $DRY_RUN; then
    echo "  [dry-run] Would check API health"
else
    for i in $(seq 1 30); do
        if curl -sSf "http://127.0.0.1:8000/api/health" >/dev/null 2>&1; then
            ok "API healthy"
            break
        fi
        if [ "$i" -eq 30 ]; then
            fail "API health check failed after 30 attempts — rolling back"
            cp "$BACKUP" .env
            docker compose "${COMPOSE_FILES[@]}" up -d --wait app worker 2>&1 | tail -3
            fail "Upgrade aborted. Previous version restored."
            exit 1
        fi
        sleep 2
    done
fi

# ── done ────────────────────────────────────────────────────────────────────

echo ""
echo -e "${GREEN}${BOLD}Upgrade complete.${NC}"
echo "Backup saved to ${BACKUP}"
echo "Run ${CYAN}./doctor.sh${NC} for a full health check."
