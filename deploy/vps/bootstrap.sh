#!/usr/bin/env bash
# One-shot Python env on Ubuntu VPS (run from repo root after git clone).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
if [[ ! -f app.py ]]; then
  echo "Expected app.py in $ROOT — run this from a cloned ict-trading-dashboard repo."
  exit 1
fi
python3 -m venv venv
# shellcheck disable=SC1091
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
if [[ ! -f .env ]]; then
  cp deploy/vps/.env.example .env
  echo "Created .env from deploy/vps/.env.example — edit secrets and FLASK_SECRET_KEY."
fi
echo "Done. Next:"
echo "  1) Edit .env (FLASK_DEBUG=0 on cloud)"
echo "  2) sudo cp deploy/vps/ict-dashboard.service /etc/systemd/system/ict-dashboard.service"
echo "  3) Edit that unit if your user/path is not /home/trader/ict-trading-dashboard"
echo "  4) sudo systemctl daemon-reload && sudo systemctl enable --now ict-dashboard"
echo "  5) Chief QE (optional): source venv/bin/activate && python scripts/chief_qe_sweep.py | tee chief_qe.log"
