#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
DRY_RUN=false
VERSION=""
BACKUP_DIR=""
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=true ;;
        --version=*) VERSION="${arg#*=}" ;;
        --backup-dir=*) BACKUP_DIR="${arg#*=}" ;;
        --*) echo "Unknown argument: $arg" >&2; exit 2 ;;
        *) [ -z "$VERSION" ] || { echo "Specify one target version" >&2; exit 2; }; VERSION="$arg" ;;
    esac
done
[ -n "$VERSION" ] || { echo "Usage: ./upgrade.sh [--dry-run] [--backup-dir=PATH] VERSION" >&2; exit 2; }
case "$VERSION" in latest|main|master) echo "Refusing mutable application tag: $VERSION" >&2; exit 2;; esac
[ -f .env ] || { echo ".env missing; run ./setup.sh first" >&2; exit 1; }
BACKUP_DIR="${BACKUP_DIR:-backups/pre-upgrade-$(date -u +%Y%m%dT%H%M%SZ)}"

if $DRY_RUN; then
    ./backup.sh --dry-run --output="$BACKUP_DIR"
    cat <<EOF
[dry-run] would set APP_VERSION=${VERSION}, pull app/worker/beat, quiesce writers,
[dry-run] migrate, restart, and verify health; recovery artifact: ${BACKUP_DIR}
EOF
    exit 0
fi

./backup.sh --output="$BACKUP_DIR"
previous_env=".env.pre-upgrade-$(date -u +%Y%m%dT%H%M%SZ)"
install -m 600 .env "$previous_env"
TARGET_VERSION="$VERSION" python3 - <<'PY'
import os
from pathlib import Path
path = Path(".env")
lines = path.read_text().splitlines()
key = "APP_VERSION"
value = os.environ["TARGET_VERSION"]
out = []
replaced = False
for line in lines:
    if line.startswith(key + "="):
        out.append(f"{key}={value}")
        replaced = True
    else:
        out.append(line)
if not replaced:
    out.append(f"{key}={value}")
path.write_text("\n".join(out) + "\n")
PY

recover() {
    echo "Upgrade failed. Database/data rollback requires the verified backup." >&2
    echo "Recovery: ./restore.sh --yes '$BACKUP_DIR'" >&2
    echo "Previous configuration: $previous_env" >&2
}
trap recover ERR

docker compose config --quiet
docker compose pull app worker beat
docker compose stop app worker beat
docker compose run --rm -T app alembic upgrade head
docker compose up -d --wait app worker beat
app_port="$(awk -F= '$1 == "APP_PORT" {sub(/^[^=]*=/, ""); print; exit}' .env)"
curl -fsS "http://127.0.0.1:${app_port:-8000}/api/health" >/dev/null
./doctor.sh --quick
trap - ERR

echo "Upgrade to ${VERSION} complete."
echo "Verified pre-upgrade backup: ${BACKUP_DIR}"
echo "Previous configuration: ${previous_env}"
