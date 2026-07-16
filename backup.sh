#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
DRY_RUN=false
OUTPUT=""
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=true ;;
        --output=*) OUTPUT="${arg#*=}" ;;
        *) echo "Unknown argument: $arg" >&2; exit 2 ;;
    esac
done
[ -f .env ] || { echo ".env is missing; run ./setup.sh first" >&2; exit 1; }
command -v docker >/dev/null || { echo "docker is required" >&2; exit 1; }
command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 1; }
docker compose version >/dev/null
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
OUTPUT="${OUTPUT:-backups/library-${stamp}}"

if $DRY_RUN; then
    cat <<EOF
[dry-run] would quiesce app, worker, and beat
[dry-run] would create PostgreSQL logical dump and cold Qdrant/SeaweedFS archives
[dry-run] would write checksums and manifest to ${OUTPUT}
[dry-run] would restart only services that were running
EOF
    exit 0
fi
[ ! -e "$OUTPUT" ] || { echo "Backup destination already exists: $OUTPUT" >&2; exit 1; }
mkdir -p "$OUTPUT"
OUTPUT_ABS="$(cd "$OUTPUT" && pwd)"
chmod 700 "$OUTPUT_ABS"

running=()
for service in app worker beat; do
    cid="$(docker compose ps -q "$service" 2>/dev/null || true)"
    if [ -n "$cid" ] && [ "$(docker inspect "$cid" --format '{{.State.Running}}')" = true ]; then running+=("$service"); fi
done
restart_services() {
    docker compose up -d --wait postgres redis qdrant seaweedfs ollama >/dev/null 2>&1 || true
    if [ "${#running[@]}" -gt 0 ]; then docker compose up -d --wait "${running[@]}" >/dev/null 2>&1 || true; fi
}
trap restart_services EXIT

echo "Quiescing writers..."
docker compose stop app worker beat >/dev/null 2>&1 || true
docker compose up -d --wait postgres qdrant seaweedfs >/dev/null

postgres_user="$(docker compose exec -T postgres sh -c 'printf %s "$POSTGRES_USER"')"
postgres_db="$(docker compose exec -T postgres sh -c 'printf %s "$POSTGRES_DB"')"
docker compose exec -T postgres pg_dump -U "$postgres_user" -d "$postgres_db" -Fc > "$OUTPUT_ABS/postgres.dump"
schema_revision="$(docker compose exec -T postgres psql -U "$postgres_user" -d "$postgres_db" -Atc 'select version_num from alembic_version' 2>/dev/null || echo unknown)"

qdrant_id="$(docker compose ps -q qdrant)"
seaweed_id="$(docker compose ps -q seaweedfs)"
qdrant_volume="$(docker inspect "$qdrant_id" --format '{{range .Mounts}}{{if eq .Destination "/qdrant/storage"}}{{.Name}}{{end}}{{end}}')"
seaweed_volume="$(docker inspect "$seaweed_id" --format '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Name}}{{end}}{{end}}')"
[ -n "$qdrant_volume" ] && [ -n "$seaweed_volume" ] || { echo "Could not resolve managed data volumes" >&2; exit 1; }

docker compose stop qdrant seaweedfs >/dev/null
docker run --rm -v "${qdrant_volume}:/source:ro" -v "${OUTPUT_ABS}:/backup" alpine:3.22 tar -czf /backup/qdrant.tar.gz -C /source .
docker run --rm -v "${seaweed_volume}:/source:ro" -v "${OUTPUT_ABS}:/backup" alpine:3.22 tar -czf /backup/seaweedfs.tar.gz -C /source .
install -m 600 .env "$OUTPUT_ABS/config.env"

APP_VERSION_VALUE="$(awk -F= '$1 == "APP_VERSION" {sub(/^[^=]*=/, ""); print; exit}' .env)" \
SCHEMA_REVISION="$schema_revision" BACKUP_STAMP="$stamp" BACKUP_DIR="$OUTPUT_ABS" \
python3 - <<'PY'
import hashlib, json, os
from pathlib import Path
root = Path(os.environ["BACKUP_DIR"])
files = ["postgres.dump", "qdrant.tar.gz", "seaweedfs.tar.gz", "config.env"]
checksums = {}
for name in files:
    digest = hashlib.sha256((root / name).read_bytes()).hexdigest()
    checksums[name] = digest
(root / "checksums.sha256").write_text("".join(f"{digest}  {name}\n" for name, digest in checksums.items()))
manifest = {
    "format": 1,
    "created_at": os.environ["BACKUP_STAMP"],
    "app_version": os.environ.get("APP_VERSION_VALUE") or "unknown",
    "schema_revision": os.environ["SCHEMA_REVISION"].strip() or "unknown",
    "components": ["postgres", "qdrant", "seaweedfs", "config"],
    "excluded_reconstructible": ["redis", "ollama_data", "application_cache"],
    "checksums": checksums,
}
(root / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
PY
chmod 600 "$OUTPUT_ABS"/*
trap - EXIT
restart_services
echo "Backup complete: $OUTPUT_ABS"
echo "Treat this directory as sensitive because config.env contains deployment credentials."
