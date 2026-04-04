#!/usr/bin/env bash
# Quick-start the dashboard app on the VPS, then verify /api/health on port 5050.
# Auto-detects repo dir: ~/autoresearchclaw or ~/ict-trading-dashboard.
#
# Usage:
#   bash deploy/vps/start-dashboard-and-verify.sh
#
set -euo pipefail

DIR=""
if [[ -f "$HOME/ict-trading-dashboard/app.py" ]]; then
  DIR="$HOME/ict-trading-dashboard"
elif [[ -f "$HOME/autoresearchclaw/app.py" ]]; then
  DIR="$HOME/autoresearchclaw"
fi

if [[ -z "$DIR" ]]; then
  echo "[start-dashboard] Could not find app.py in ~/autoresearchclaw or ~/ict-trading-dashboard"
  exit 1
fi

echo "[start-dashboard] Using repo: $DIR"
cd "$DIR"

PORT="${PORT:-5050}"
if [[ -f .env ]]; then
  # If .env defines PORT, respect it.
  line="$(grep -E '^[[:space:]]*PORT=' .env | tail -1 || true)"
  if [[ -n "${line}" ]]; then
    PORT="${line#*=}"
    PORT="${PORT//\"/}"
    PORT="${PORT//\'/}"
  fi
fi

if [[ ! -d venv ]]; then
  python3 -m venv venv
fi

# shellcheck disable=SC1091
source venv/bin/activate

if [[ -f requirements.txt ]]; then
  pip install -q -r requirements.txt
fi

export FLASK_DEBUG=0
export PORT="$PORT"

echo "[start-dashboard] Starting python app.py on port $PORT (background)…"
nohup python app.py >> "$HOME/ict-dashboard.log" 2>&1 &

ok=0
for _ in $(seq 1 30); do
  if curl -sf "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1; then
    ok=1
    break
  fi
  sleep 2
done

if [[ "$ok" -ne 1 ]]; then
  echo "[start-dashboard] Health check failed. Tail log:"
  tail -n 80 "$HOME/ict-dashboard.log" || true
  exit 1
fi

echo "[start-dashboard] OK: http://127.0.0.1:${PORT}/api/health"
curl -sS "http://127.0.0.1:${PORT}/api/health" | head -c 300 || true
echo

