#!/usr/bin/env bash
# Local dev: Flask + Socket.IO on PORT (default 5050), paper loop starts automatically.
# Open: http://127.0.0.1:${PORT:-5050}/live
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PAPER_TRADING_AUTOSTART="${PAPER_TRADING_AUTOSTART:-1}"
export PORT="${PORT:-5050}"
exec python3 app.py
