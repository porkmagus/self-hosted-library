#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
DRY_RUN=false
YES=false
KEEP_CONFIG=false
BACKUP=""
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=true ;;
        --yes) YES=true ;;
        --keep-config) KEEP_CONFIG=true ;;
        --*) echo "Unknown argument: $arg" >&2; exit 2 ;;
        *) [ -z "$BACKUP" ] || { echo "Specify one backup directory" >&2; exit 2; }; BACKUP="$arg" ;;
    esac
done
[ -n "$BACKUP" ] || { echo "Usage: ./restore.sh [--dry-run] [--yes] [--keep-config] BACKUP_DIR" >&2; exit 2; }
BACKUP="$(cd "$BACKUP" && pwd)"
for file in manifest.json checksums.sha256 postgres.dump qdrant.tar.gz seaweedfs.tar.gz config.env; do [ -f "$BACKUP/$file" ] || { echo "Missing backup file: $file" >&2; exit 1; }; done
command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 1; }

BACKUP_DIR="$BACKUP" python3 - <<'PY'
import hashlib, json, os
from pathlib import Path
root = Path(os.environ["BACKUP_DIR"])
manifest = json.loads((root / "manifest.json").read_text())
if manifest.get("format") != 1:
    raise SystemExit("Unsupported backup format")
for name, expected in manifest["checksums"].items():
    actual = hashlib.sha256((root / name).read_bytes()).hexdigest()
    if actual != expected:
        raise SystemExit(f"Checksum mismatch: {name}")
print(f"Verified backup {manifest['created_at']} (app {manifest['app_version']}, schema {manifest['schema_revision']})")
PY

if $DRY_RUN; then
    cat <<EOF
[dry-run] would stop this Compose project
[dry-run] would restore config, PostgreSQL, Qdrant, and SeaweedFS from ${BACKUP}
[dry-run] would migrate, start services, and run doctor
EOF
    exit 0
fi
if ! $YES; then
    cat <<'EOF'
WARNING: restore replaces this project's PostgreSQL, Qdrant, and SeaweedFS data.
EOF
    read -r -p "Type 'RESTORE LIBRARY BACKUP' to continue: " answer
    [ "$answer" = "RESTORE LIBRARY BACKUP" ] || { echo "Aborted."; exit 0; }
fi
command -v docker >/dev/null || { echo "docker is required" >&2; exit 1; }
docker compose version >/dev/null

if ! $KEEP_CONFIG; then
    if [ -f .env ]; then install -m 600 .env ".env.pre-restore-$(date -u +%Y%m%dT%H%M%SZ)"; fi
    install -m 600 "$BACKUP/config.env" .env
fi
[ -f .env ] || { echo "No .env available; omit --keep-config or create compatible configuration" >&2; exit 1; }
mkdir -p .state
S3_ACCESS_KEY_VALUE="$(awk -F= '$1 == "S3_ACCESS_KEY" {sub(/^[^=]*=/, ""); print; exit}' .env)" \
S3_SECRET_KEY_VALUE="$(awk -F= '$1 == "S3_SECRET_KEY" {sub(/^[^=]*=/, ""); print; exit}' .env)" \
python3 -c 'import json, os, pathlib
p = pathlib.Path(".state/seaweedfs-s3.json")
p.write_text(json.dumps({"identities": [{"name": "library", "credentials": [{"accessKey": os.environ["S3_ACCESS_KEY_VALUE"], "secretKey": os.environ["S3_SECRET_KEY_VALUE"]}], "actions": ["Admin", "Read", "Write", "List", "Tagging"]}]}, separators=(",", ":")) + "\n")'
chmod 600 .state/seaweedfs-s3.json

docker compose down --remove-orphans
docker compose up -d postgres qdrant seaweedfs
qdrant_id="$(docker compose ps -q qdrant)"
seaweed_id="$(docker compose ps -q seaweedfs)"
qdrant_volume="$(docker inspect "$qdrant_id" --format '{{range .Mounts}}{{if eq .Destination "/qdrant/storage"}}{{.Name}}{{end}}{{end}}')"
seaweed_volume="$(docker inspect "$seaweed_id" --format '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Name}}{{end}}{{end}}')"
[ -n "$qdrant_volume" ] && [ -n "$seaweed_volume" ] || { echo "Could not resolve managed volumes" >&2; exit 1; }
docker compose stop qdrant seaweedfs >/dev/null
for spec in "${qdrant_volume}:qdrant.tar.gz" "${seaweed_volume}:seaweedfs.tar.gz"; do
    volume="${spec%%:*}"; archive="${spec#*:}"
    docker run --rm -v "${volume}:/target" -v "${BACKUP}:/backup:ro" alpine:3.22 sh -ec "find /target -mindepth 1 -maxdepth 1 -exec rm -rf {} +; tar -xzf /backup/${archive} -C /target"
done

docker compose up -d --wait postgres qdrant seaweedfs redis ollama
postgres_user="$(docker compose exec -T postgres sh -c 'printf %s "$POSTGRES_USER"')"
postgres_db="$(docker compose exec -T postgres sh -c 'printf %s "$POSTGRES_DB"')"
docker compose exec -T postgres pg_restore -U "$postgres_user" -d "$postgres_db" --clean --if-exists < "$BACKUP/postgres.dump"
docker compose run --rm -T app alembic upgrade head
docker compose run --rm -T app python -c 'from api.services.object_store import get_object_store; assert get_object_store().bucket_exists()'
docker compose up -d --wait app worker beat
./doctor.sh --quick

echo "Restore complete from: $BACKUP"
