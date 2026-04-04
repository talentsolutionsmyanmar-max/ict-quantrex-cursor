#!/usr/bin/env bash
# One-shot dev setup: venv, dependencies, .env stub, smoke import.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PY="${PYTHON:-python3}"
if ! command -v "$PY" &>/dev/null; then
  echo "Need python3 on PATH (or set PYTHON=/path/to/python3)" >&2
  exit 1
fi

VENV="$ROOT/venv"
if [[ ! -x "$VENV/bin/python" ]]; then
  echo "Creating venv at $VENV ..."
  "$PY" -m venv "$VENV"
fi

PIP="$VENV/bin/pip"
"$PIP" install -q -U pip
"$PIP" install -q -r "$ROOT/requirements.txt"

if [[ ! -f "$ROOT/.env" && -f "$ROOT/.env.example" ]]; then
  cp "$ROOT/.env.example" "$ROOT/.env"
  echo "Created .env from .env.example (edit secrets if needed)."
fi

echo "Smoke test: load config + spec ..."
"$VENV/bin/python" <<'PY'
from config import build_config
c = build_config()
assert c.SYMBOL
print("  SYMBOL=", c.SYMBOL, " TIMEFRAME=", c.TIMEFRAME, sep="")
from research_lab import clone_config_genes
c2 = clone_config_genes(c)
assert c2.REGIME_GATE_ENABLED == c.REGIME_GATE_ENABLED
print("  clone_config_genes: REGIME_* OK")
print("Setup OK. Run: source venv/bin/activate && python app.py")
PY
