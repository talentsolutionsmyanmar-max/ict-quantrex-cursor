#!/usr/bin/env bash
# One shot on the droplet: sync + restart + wait for health (+ optional Chief QE).
# Usage:  bash deploy/vps/move-and-fly.sh
#         MOVE_AND_FLY_QE=1 bash deploy/vps/move-and-fly.sh
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

bash deploy/vps/sync-and-restart.sh

PORT=5050
if [[ -f .env ]]; then
  line="$(grep -E '^[[:space:]]*PORT=' .env | tail -1 || true)"
  if [[ -n "${line}" ]]; then
    PORT="${line#*=}"
    PORT="${PORT//\"/}"
    PORT="${PORT//\'/}"
  fi
fi

echo "[move-and-fly] probing http://127.0.0.1:${PORT}/api/health …"
ok=0
for i in $(seq 1 30); do
  if curl -sf "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1; then
    ok=1
    break
  fi
  sleep 2
done

if [[ "$ok" -ne 1 ]]; then
  echo "[move-and-fly] health timeout — try: sudo journalctl -u ict-dashboard -n 100 --no-pager"
  exit 1
fi

curl -sS "http://127.0.0.1:${PORT}/api/health" | head -c 400 || true
echo ""
echo "[move-and-fly] health OK"

if [[ "${MOVE_AND_FLY_QE:-0}" == "1" ]]; then
  echo "[move-and-fly] MOVE_AND_FLY_QE=1 — running scripts/chief_qe_sweep.py (can take a long time) …"
  # shellcheck disable=SC1091
  source venv/bin/activate
  python scripts/chief_qe_sweep.py 2>&1 | tee -a chief_qe_fly.log
fi

echo "[move-and-fly] airborne — open dashboard on :${PORT}"
