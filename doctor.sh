#!/usr/bin/env bash
set -euo pipefail

# ── Self-Hosted Library health check ────────────────────────────────────────
# Verifies every component of a running deployment.
#
# Usage:
#   ./doctor.sh            # full check
#   ./doctor.sh --quick     # skip model pull and embedding test
#   ./doctor.sh --json      # machine-readable output
# ────────────────────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

QUICK=false
JSON_OUT=false
for arg in "$@"; do
    case "$arg" in
        --quick) QUICK=true ;;
        --json)  JSON_OUT=true ;;
        *) echo "Unknown argument: $arg"; exit 1 ;;
    esac
done

PASS=0
FAIL=0
WARN=0
RESULTS=()

pass() { PASS=$((PASS+1)); RESULTS+=("{\"check\":\"$1\",\"status\":\"pass\",\"detail\":\"$2\"}"); $JSON_OUT || echo -e "  ${GREEN}✓${NC} $1 — $2"; }
fail() { FAIL=$((FAIL+1)); RESULTS+=("{\"check\":\"$1\",\"status\":\"fail\",\"detail\":\"$2\"}"); $JSON_OUT || echo -e "  ${RED}✗${NC} $1 — $2"; }
warn() { WARN=$((WARN+1)); RESULTS+=("{\"check\":\"$1\",\"status\":\"warn\",\"detail\":\"$2\"}"); $JSON_OUT || echo -e "  ${YELLOW}⚠${NC} $1 — $2"; }

$JSON_OUT || echo -e "${BOLD}Self-Hosted Library Doctor${NC}\n"

# ── Docker ──────────────────────────────────────────────────────────────────

$JSON_OUT || echo -e "${CYAN}Docker${NC}"

if command -v docker >/dev/null 2>&1; then
    if docker info >/dev/null 2>&1; then
        pass "docker" "running ($(docker info --format '{{.ServerVersion}}' 2>/dev/null || echo unknown))"
    else
        fail "docker" "daemon not running or permission denied"
    fi
else
    fail "docker" "not installed"
fi

# ── GPU ─────────────────────────────────────────────────────────────────────

$JSON_OUT || echo -e "${CYAN}GPU${NC}"

if command -v nvidia-smi >/dev/null 2>&1; then
    if nvidia-smi -L >/dev/null 2>&1; then
        VRAM=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -d ' ' || echo 0)
        DRIVER=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null | head -1 || echo unknown)
        pass "nvidia-gpu" "driver ${DRIVER}, ${VRAM} MiB VRAM"

        if docker run --rm --gpus all nvidia/cuda:12.6-base-ubuntu24.04 nvidia-smi >/dev/null 2>&1; then
            pass "nvidia-container-toolkit" "GPU available in containers"
        else
            warn "nvidia-container-toolkit" "not configured — GPU unavailable in containers"
        fi
    else
        warn "nvidia-gpu" "nvidia-smi found but no usable GPU"
    fi
else
    warn "nvidia-gpu" "not detected — CPU-only mode"
fi

# ── Disk ────────────────────────────────────────────────────────────────────

$JSON_OUT || echo -e "${CYAN}Disk${NC}"

AVAIL_GB=$(df -BG . | awk 'NR==2 {print $4}' | tr -d 'G')
if [ "$AVAIL_GB" -lt 10 ]; then
    fail "disk" "${AVAIL_GB} GB free (need >10 GB)"
elif [ "$AVAIL_GB" -lt 20 ]; then
    warn "disk" "${AVAIL_GB} GB free (recommend >20 GB)"
else
    pass "disk" "${AVAIL_GB} GB free"
fi

# ── Ports ───────────────────────────────────────────────────────────────────

$JSON_OUT || echo -e "${CYAN}Ports${NC}"

for port in 3000 8000 5432 6379 6333 9000 11434; do
    if ss -tlnp 2>/dev/null | grep -q ":${port} " || netstat -tlnp 2>/dev/null | grep -q ":${port} "; then
        pass "port-${port}" "listening"
    else
        fail "port-${port}" "not listening"
    fi
done

# ── Containers ──────────────────────────────────────────────────────────────

$JSON_OUT || echo -e "${CYAN}Containers${NC}"

EXPECTED=(qdrant postgres redis minio ollama api worker web)
for svc in "${EXPECTED[@]}"; do
    if docker compose ps --format json 2>/dev/null | grep -q "\"Service\":\"${svc}\""; then
        HEALTH=$(docker inspect "$(docker compose ps -q "$svc" 2>/dev/null)" --format '{{.State.Health.Status}}' 2>/dev/null || echo unknown)
        if [ "$HEALTH" = "healthy" ]; then
            pass "container-${svc}" "healthy"
        else
            warn "container-${svc}" "status=${HEALTH}"
        fi
    else
        fail "container-${svc}" "not running"
    fi
done

# ── API ─────────────────────────────────────────────────────────────────────

$JSON_OUT || echo -e "${CYAN}API${NC}"

if HEALTH=$(curl -sSf "http://127.0.0.1:8000/api/health" 2>/dev/null); then
    pass "api-health" "responding"
    for dep in ollama qdrant redis postgres minio; do
        STATUS=$(echo "$HEALTH" | python3 -c "import sys,json; print(json.load(sys.stdin).get('${dep}','unknown'))" 2>/dev/null || echo unknown)
        if [ "$STATUS" = "ok" ]; then
            pass "api-${dep}" "ok"
        else
            warn "api-${dep}" "${STATUS}"
        fi
    done
else
    fail "api-health" "unreachable"
fi

# ── Embedding ───────────────────────────────────────────────────────────────

if ! $QUICK; then
    $JSON_OUT || echo -e "${CYAN}Embedding${NC}"

    if curl -sSf "http://127.0.0.1:11434/api/tags" 2>/dev/null | python3 -c "import sys,json; models=[m['name'] for m in json.load(sys.stdin).get('models',[])]; sys.exit(0 if 'bge-large:latest' in models else 1)" 2>/dev/null; then
        pass "ollama-model" "bge-large present"
    else
        warn "ollama-model" "bge-large not pulled — run: curl http://127.0.0.1:11434/api/pull -d '{\"name\":\"bge-large\",\"stream\":false}'"
    fi

    if EMBED=$(curl -sSf "http://127.0.0.1:11434/api/embed" -d '{"model":"bge-large","input":"test"}' 2>/dev/null); then
        DIM=$(echo "$EMBED" | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('embeddings',[[]])[0]))" 2>/dev/null || echo 0)
        if [ "$DIM" -eq 1024 ]; then
            pass "embedding" "1024-dim vectors working"
        else
            fail "embedding" "unexpected dimension: ${DIM}"
        fi
    else
        fail "embedding" "endpoint unreachable"
    fi
fi

# ── summary ─────────────────────────────────────────────────────────────────

if $JSON_OUT; then
    echo "[${RESULTS[*]}]" | sed 's/} {/}, {/g'
else
    echo ""
    TOTAL=$((PASS + FAIL + WARN))
    echo -e "${BOLD}Results:${NC} ${GREEN}${PASS} pass${NC}, ${YELLOW}${WARN} warn${NC}, ${RED}${FAIL} fail${NC} (${TOTAL} checks)"
    if [ "$FAIL" -gt 0 ]; then
        echo -e "${RED}System has issues — review failures above.${NC}"
        exit 1
    elif [ "$WARN" -gt 0 ]; then
        echo -e "${YELLOW}System is operational with warnings.${NC}"
    else
        echo -e "${GREEN}All checks passed.${NC}"
    fi
fi
