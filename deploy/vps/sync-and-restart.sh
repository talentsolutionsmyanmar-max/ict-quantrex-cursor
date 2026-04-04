#!/usr/bin/env bash
# On the droplet: pull latest code, refresh deps, restart dashboard service.
# Requires: repo at expected path, venv present, passwordless sudo for systemctl (or run restart manually).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
if [[ ! -f app.py ]]; then
  echo "No app.py in $ROOT"
  exit 1
fi
git pull --ff-only
# shellcheck disable=SC1091
source venv/bin/activate
pip install -q -r requirements.txt
sudo systemctl restart ict-dashboard
sudo systemctl status ict-dashboard --no-pager
