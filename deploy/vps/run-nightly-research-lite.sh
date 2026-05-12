#!/usr/bin/env bash
# Nightly low-cost research job for small VPS nodes.
# Purpose: run a lighter Chief QE sweep off-peak without impacting daytime paper/live loop.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

if [[ ! -f app.py ]]; then
  echo "No app.py in $ROOT — fix WorkingDirectory / clone path." >&2
  exit 1
fi

mkdir -p logs
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG="logs/nightly-research-lite-${STAMP}.log"
ln -sf "$(basename "$LOG")" logs/nightly-research-lite-latest.log

if [[ ! -x "$ROOT/venv/bin/python" ]]; then
  echo "Missing venv: $ROOT/venv/bin/python" >&2
  exit 1
fi

set -a
[[ -f .env ]] && source .env
set +a

# Budget-safe defaults: fewer symbols, short lookback, fewer windows, modest friction stress.
: "${SYMBOLS:=BTCUSDT,ETHUSDT,SOLUSDT}"
: "${START_DATE:=2025-01-01}"
: "${END_DATE:=2025-03-31}"
: "${TIMEFRAME:=1h}"
: "${N_WINDOWS:=2}"
: "${FRICTION_MULT:=1.3}"

export SYMBOLS START_DATE END_DATE TIMEFRAME N_WINDOWS FRICTION_MULT

{
  echo "===== nightly-research-lite start ${STAMP} UTC ====="
  echo "PWD=$PWD"
  echo "SYMBOLS=${SYMBOLS}"
  echo "RANGE=${START_DATE}..${END_DATE} TF=${TIMEFRAME} N_WINDOWS=${N_WINDOWS} FRICTION_MULT=${FRICTION_MULT}"
} | tee "$LOG"

set +e
"$ROOT/venv/bin/python" scripts/chief_qe_sweep.py 2>&1 | tee -a "$LOG"
EXIT=${PIPESTATUS[0]}
set -e

{
  echo "===== nightly-research-lite exit ${EXIT} ====="
} | tee -a "$LOG"

exit "$EXIT"
