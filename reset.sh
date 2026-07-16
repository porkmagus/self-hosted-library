#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
REMOVE_VOLUMES=false
REMOVE_CONFIG=false
DRY_RUN=false
AUTOMATION_OVERRIDE=false
for arg in "$@"; do
    case "$arg" in
        --volumes) REMOVE_VOLUMES=true ;;
        --hard) REMOVE_VOLUMES=true; REMOVE_CONFIG=true ;;
        --dry-run) DRY_RUN=true ;;
        --yes-i-really-mean-it) AUTOMATION_OVERRIDE=true ;;
        *) echo "Unknown argument: $arg" >&2; exit 2 ;;
    esac
done

run() { if $DRY_RUN; then printf '[dry-run]'; printf ' %q' "$@"; printf '\n'; else "$@"; fi; }

if $REMOVE_VOLUMES && ! $DRY_RUN && ! $AUTOMATION_OVERRIDE; then
    cat <<'EOF'
WARNING: this permanently deletes PostgreSQL, Qdrant, SeaweedFS objects,
Redis state, downloaded Ollama models, and model caches managed by this project.
EOF
    read -r -p "Type 'DELETE ALL LIBRARY DATA' to continue: " answer
    [ "$answer" = "DELETE ALL LIBRARY DATA" ] || { echo "Aborted."; exit 0; }
fi

if $REMOVE_VOLUMES; then
    run docker compose down -v --remove-orphans
else
    run docker compose down --remove-orphans
fi

if $REMOVE_CONFIG; then
    run rm -f .env .state/seaweedfs-s3.json
    if ! $DRY_RUN; then rmdir .state 2>/dev/null || true; fi
fi

if $DRY_RUN; then
    echo "Dry run complete; nothing changed."
elif $REMOVE_CONFIG; then
    echo "Factory reset complete. Run ./setup.sh to create a new deployment."
elif $REMOVE_VOLUMES; then
    echo "Managed data volumes removed. Run ./setup.sh to start clean."
else
    echo "Services stopped; managed data was preserved."
fi
