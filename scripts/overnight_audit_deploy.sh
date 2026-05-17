#!/usr/bin/env bash
# VPS: pull audit FVG/sweep fields, restart paper + DOM/CVD reporter, verify processes.
set -euo pipefail
cd ~/ict-quantrex-cursor

export OVERNIGHT_DEPLOY_UTC="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "overnight_deploy_utc=${OVERNIGHT_DEPLOY_UTC}" | tee logs/overnight_deploy.txt

git pull --ff-only
# shellcheck disable=SC1091
source venv/bin/activate
pip install -q -r requirements.txt

mkdir -p logs reports data

tmux kill-session -t paper 2>/dev/null || true
tmux new-session -d -s paper bash -lc \
  'cd ~/ict-quantrex-cursor && exec ./venv/bin/python -u paper_trader.py --live --symbol BTC/USDT --live-seconds 86400 >> logs/paper_live_stdout.log 2>&1'

tmux kill-session -t dom_cvd 2>/dev/null || true
tmux new-session -d -s dom_cvd bash -lc \
  'cd ~/ict-quantrex-cursor && exec ./venv/bin/python -u scripts/dom_cvd_auto_reporter.py >> logs/dom_cvd_reporter.log 2>&1'

sleep 45

echo "=== PROCESSES ==="
pgrep -af 'paper_trader.py.*--live' || echo 'WARN: paper_trader missing'
pgrep -af 'dom_cvd_auto_reporter' || echo 'WARN: dom_cvd reporter missing'
tmux ls 2>/dev/null || true

echo "=== PAPER LOG (tail) ==="
tail -n 8 logs/paper_live_stdout.log 2>/dev/null || true

echo "=== DOM/CVD LOG (tail) ==="
tail -n 8 logs/dom_cvd_reporter.log 2>/dev/null || true

echo "=== SIGNAL AUDIT (last line) ==="
tail -n 1 data/signal_audit.jsonl 2>/dev/null || echo '(no audit yet — wait for next candle)'

echo "=== DOM/CVD TRACKER (last line) ==="
tail -n 1 reports/dom_cvd_4h_tracker.jsonl 2>/dev/null || echo '(no tracker yet)'

echo "=== REALITY CHECK (48h; may be empty right after deploy) ==="
./venv/bin/python scripts/ranging_fvg_sweep_reality_check.py || true

python - <<'PY'
import json
from datetime import datetime, timezone
from pathlib import Path
root = Path(".")
out = {
    "deployed_at_utc": datetime.now(timezone.utc).isoformat(),
    "paper_running": bool(__import__("subprocess").run(
        ["pgrep", "-f", "paper_trader.py.*--live"], capture_output=True
    ).returncode == 0),
    "dom_cvd_running": bool(__import__("subprocess").run(
        ["pgrep", "-f", "dom_cvd_auto_reporter"], capture_output=True
    ).returncode == 0),
}
audit = root / "data" / "signal_audit.jsonl"
if audit.is_file():
    lines = [ln for ln in audit.read_text(encoding="utf-8").splitlines() if ln.strip()]
    out["signal_audit_lines"] = len(lines)
    if lines:
        out["signal_audit_last"] = json.loads(lines[-1])
tracker = root / "reports" / "dom_cvd_4h_tracker.jsonl"
if tracker.is_file():
    tlines = [ln for ln in tracker.read_text(encoding="utf-8").splitlines() if ln.strip()]
    out["dom_cvd_tracker_lines"] = len(tlines)
    if tlines:
        out["dom_cvd_tracker_last"] = json.loads(tlines[-1])
report = root / "reports" / "overnight_status.json"
report.write_text(json.dumps(out, indent=2), encoding="utf-8")
print("Wrote", report)
PY
