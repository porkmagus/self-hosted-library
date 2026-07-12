#!/usr/bin/env bash
set -euo pipefail

# ── Self-Hosted Library reset ───────────────────────────────────────────────
# Stops all services and optionally removes persistent data.
#
# Usage:
#   ./reset.sh              # stop containers, keep data
#   ./reset.sh --volumes     # stop containers + remove all volumes (fresh start)
#   ./reset.sh --hard        # stop + volumes + .env (complete factory reset)
#   ./reset.sh --dry-run     # print what would happen
# ────────────────────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

REMOVE_VOLUMES=false
REMOVE_ENV=false
DRY_RUN=false

for arg in "$@"; do
    case "$arg" in
        --volumes) REMOVE_VOLUMES=true ;;
        --hard)    REMOVE_VOLUMES=true; REMOVE_ENV=true ;;
        --dry-run) DRY_RUN=true ;;
        *) echo "Unknown argument: $arg"; exit 1 ;;
    esac
done

say()   { echo -e "${CYAN}→${NC} $*"; }
ok()    { echo -e "  ${GREEN}✓${NC} $*"; }
warn()  { echo -e "  ${YELLOW}⚠${NC} $*"; }

# ── confirmation ────────────────────────────────────────────────────────────

if $REMOVE_VOLUMES && ! $DRY_RUN; then
    echo -e "${RED}${BOLD}WARNING: This will permanently delete all library data.${NC}"
    echo "  - All ingested documents and indexes"
    echo "  - All settings and metadata"
    echo "  - All uploaded files"
    if $REMOVE_ENV; then
        echo "  - .env configuration file"
    fi
    echo ""
    read -rp "Type 'yes' to confirm: " yn
    if [ "$yn" != "yes" ]; then
        say "Aborted."
        exit 0
    fi
fi

# ── stop containers ─────────────────────────────────────────────────────────

say "Stopping all services..."

if $DRY_RUN; then
    echo "  [dry-run] Would run: docker compose down"
else
    docker compose down --remove-orphans 2>/dev/null || true
    ok "Containers stopped"
fi

# ── remove volumes ──────────────────────────────────────────────────────────

if $REMOVE_VOLUMES; then
    say "Removing persistent data..."

    VOLUMES=(
        qdrant_data
        postgres_data
        redis_data
        minio_data
        ollama_data
    )

    for vol in "${VOLUMES[@]}"; do
        full_name="$(basename "$SCRIPT_DIR" | tr '[:upper:]' '[:lower:]')_${vol}"
        if $DRY_RUN; then
            echo "  [dry-run] Would remove volume: $full_name"
        else
            docker volume rm "$full_name" 2>/dev/null || true
            ok "Removed $vol"
        fi
    done

    # Also clean local data dirs
    for d in data models; do
        if [ -d "$d" ]; then
            if $DRY_RUN; then
                echo "  [dry-run] Would remove: $d/"
            else
                rm -rf "$d"
                ok "Removed $d/"
            fi
        fi
    done
fi

# ── remove .env ─────────────────────────────────────────────────────────────

if $REMOVE_ENV; then
    if [ -f .env ]; then
        if $DRY_RUN; then
            echo "  [dry-run] Would remove: .env"
        else
            rm -f .env
            ok "Removed .env"
        fi
    fi
fi

# ── done ────────────────────────────────────────────────────────────────────

echo ""
if $DRY_RUN; then
    echo -e "${YELLOW}Dry run complete — no changes made.${NC}"
elif $REMOVE_ENV; then
    echo -e "${GREEN}Factory reset complete.${NC}"
    echo "Run ${CYAN}./setup.sh${NC} to start fresh."
elif $REMOVE_VOLUMES; then
    echo -e "${GREEN}Data reset complete.${NC}"
    echo "Run ${CYAN}docker compose up -d${NC} to restart with a clean slate."
else
    echo -e "${GREEN}Services stopped.${NC}"
    echo "Run ${CYAN}docker compose up -d${NC} to restart."
fi
