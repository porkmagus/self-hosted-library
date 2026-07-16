#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
QUICK=false
JSON_OUT=false
for arg in "$@"; do
    case "$arg" in
        --quick) QUICK=true ;;
        --json) JSON_OUT=true ;;
        *) echo "Unknown argument: $arg" >&2; exit 2 ;;
    esac
done

command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 1; }
RESULTS="$(mktemp)"
trap 'rm -f "$RESULTS"' EXIT
PASS=0; WARN=0; FAIL=0
record() {
    local status="$1" check="$2" detail="$3"
    detail="${detail//$'\t'/ }"; detail="${detail//$'\n'/ }"
    printf '%s\t%s\t%s\n' "$status" "$check" "$detail" >> "$RESULTS"
    case "$status" in pass) PASS=$((PASS+1));; warn) WARN=$((WARN+1));; fail) FAIL=$((FAIL+1));; esac
    if ! $JSON_OUT; then
        case "$status" in pass) mark="✓";; warn) mark="⚠";; fail) mark="✗";; esac
        printf '  %s %-24s %s\n' "$mark" "$check" "$detail"
    fi
}
env_value() {
    local key="$1" default="${2:-}" value
    if [ ! -f .env ]; then printf '%s' "$default"; return; fi
    value="$(awk -F= -v key="$key" '$1 == key {sub(/^[^=]*=/, ""); print; exit}' .env)"
    printf '%s' "${value:-$default}"
}

$JSON_OUT || printf 'Self-Hosted Library Doctor\n\n'
if ! command -v docker >/dev/null; then
    record fail docker "not installed"
elif ! docker info >/dev/null 2>&1; then
    record fail docker "daemon unavailable"
elif ! docker compose version >/dev/null 2>&1; then
    record fail compose "Compose v2 unavailable"
else
    record pass docker "server $(docker info --format '{{.ServerVersion}}' 2>/dev/null || echo unknown)"
    record pass compose "$(docker compose version --short 2>/dev/null || echo v2)"
fi

if [ -f .env ]; then
    mode="$(stat -f '%Lp' .env 2>/dev/null || stat -c '%a' .env 2>/dev/null || echo unknown)"
    if [ "$mode" = 600 ]; then record pass config ".env mode 0600"; else record warn config ".env mode ${mode}; expected 600"; fi
else
    record fail config ".env missing; run ./setup.sh"
fi

avail_kb="$(df -Pk . | awk 'NR==2 {print $4}')"
avail_gb=$((avail_kb / 1024 / 1024))
if [ "$avail_gb" -lt 10 ]; then record fail disk "${avail_gb} GiB free"; elif [ "$avail_gb" -lt 20 ]; then record warn disk "${avail_gb} GiB free"; else record pass disk "${avail_gb} GiB free"; fi

expected=(postgres redis qdrant seaweedfs ollama app worker beat)
for service in "${expected[@]}"; do
    cid="$(docker compose ps -q "$service" 2>/dev/null || true)"
    if [ -z "$cid" ]; then record fail "container-${service}" "not running"; continue; fi
    state="$(docker inspect "$cid" --format '{{.State.Status}}' 2>/dev/null || echo unknown)"
    health="$(docker inspect "$cid" --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' 2>/dev/null || echo unknown)"
    if [ "$state" != running ]; then record fail "container-${service}" "state=${state}"; elif [ "$health" = unhealthy ]; then record fail "container-${service}" "unhealthy"; elif [ "$health" = starting ]; then record warn "container-${service}" "health=starting"; else record pass "container-${service}" "state=${state}, health=${health}"; fi
done

app_port="$(env_value APP_PORT 8000)"
health_json="$(curl -fsS "http://127.0.0.1:${app_port}/api/health" 2>/dev/null || true)"
if [ -z "$health_json" ]; then
    record fail api-health "unreachable on configured app port ${app_port}"
else
    record pass api-health "responding on ${app_port}"
    for dependency in postgres redis qdrant ollama object_store; do
        status="$(printf '%s' "$health_json" | python3 -c 'import json,sys; print(json.load(sys.stdin).get(sys.argv[1], "missing"))' "$dependency" 2>/dev/null || echo invalid-json)"
        if [ "$status" = ok ]; then record pass "api-${dependency}" ok; else record fail "api-${dependency}" "$status"; fi
    done
fi

if ! $QUICK; then
    model="$(env_value EMBED_MODEL bge-large)"
    if docker compose exec -T ollama ollama show "$model" >/dev/null 2>&1; then record pass ollama-model "$model present"; else record warn ollama-model "$model missing; rerun ./setup.sh"; fi
    if docker compose run --rm -T app python -c 'from api.services.object_store import get_object_store; assert get_object_store().bucket_exists()' >/dev/null 2>&1; then
        record pass object-store-protocol "configured private bucket reachable"
    else
        record fail object-store-protocol "bucket/client probe failed"
    fi
fi

if $JSON_OUT; then
    python3 - "$RESULTS" "$PASS" "$WARN" "$FAIL" <<'PY'
import csv, json, sys
path, passed, warned, failed = sys.argv[1:]
with open(path, newline="") as handle:
    checks = [dict(zip(("status", "check", "detail"), row)) for row in csv.reader(handle, delimiter="\t")]
print(json.dumps({"status": "fail" if int(failed) else "ok", "summary": {"pass": int(passed), "warn": int(warned), "fail": int(failed)}, "checks": checks}, separators=(",", ":")))
PY
else
    printf '\nResults: %s pass, %s warn, %s fail\n' "$PASS" "$WARN" "$FAIL"
fi
[ "$FAIL" -eq 0 ]
