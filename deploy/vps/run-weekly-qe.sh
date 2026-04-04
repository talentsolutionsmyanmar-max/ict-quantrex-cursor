#!/usr/bin/env bash
# Chief QE weekly sweep (stability + cost stress). Use with systemd timer or cron.
# Logs: logs/weekly-qe-<UTC stamp>.log and symlink logs/weekly-qe-latest.log
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
if [[ ! -f app.py ]]; then
  echo "No app.py in $ROOT — fix WorkingDirectory / clone path." >&2
  exit 1
fi
mkdir -p logs
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG="logs/weekly-qe-${STAMP}.log"
# Point latest at this run immediately so `tail -f logs/weekly-qe-latest.log` works while it runs.
ln -sf "$(basename "$LOG")" logs/weekly-qe-latest.log
if [[ ! -x "$ROOT/venv/bin/python" ]]; then
  echo "Missing venv: $ROOT/venv/bin/python" >&2
  exit 1
fi
set -a
[[ -f .env ]] && source .env
set +a
# Default = promotion basket (SOL/ETH/BTC). Set SYMBOLS in .env for 8-coin or custom weekly runs.
: "${SYMBOLS:=SOLUSDT,ETHUSDT,BTCUSDT}"
export SYMBOLS
{
  echo "===== weekly-qe start ${STAMP} UTC ====="
  echo "PWD=$PWD"
  echo "SYMBOLS=${SYMBOLS}"
} | tee "$LOG"
set +e
"$ROOT/venv/bin/python" scripts/chief_qe_sweep.py 2>&1 | tee -a "$LOG"
EXIT=${PIPESTATUS[0]}
set -e
{
  echo "===== weekly-qe exit ${EXIT} ====="
} | tee -a "$LOG"
exit "$EXIT"
