#!/usr/bin/env python3
"""
v2.7.4 — 4-hour DOM/CVD insight auto-reporter (read-only analysis, append-only tracker).
Does not modify config, paper_trader, or app.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REPORT_INTERVAL_SEC = 4 * 3600
REPORT_LOG = ROOT / "reports" / "dom_cvd_4h_tracker.jsonl"
INSIGHTS_SCRIPT = ROOT / "scripts" / "dom_cvd_partial_insights.py"
INSIGHTS_JSON = ROOT / "reports" / "dom_cvd_partial_insights.json"


def run_insights_and_capture():
    try:
        result = subprocess.run(
            [sys.executable, str(INSIGHTS_SCRIPT)],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(ROOT),
        )
        if result.returncode != 0:
            err = (result.stderr or result.stdout or "").strip()[:200]
            print(f"Insights script failed (rc={result.returncode}): {err}")
            return None

        if not INSIGHTS_JSON.exists():
            print("No JSON output from insights (missing reports/dom_cvd_partial_insights.json).")
            return None

        data = json.loads(INSIGHTS_JSON.read_text(encoding="utf-8"))
        data["captured_at"] = datetime.now(timezone.utc).isoformat()

        REPORT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(REPORT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(data, ensure_ascii=False) + "\n")

        print(f"OK [{data['captured_at'][:19]}] DOM/CVD snapshot appended to {REPORT_LOG.name}")
        return data
    except Exception as e:
        print(f"Reporter error: {e}")
        return None


def main():
    print("DOM/CVD 4-Hour Auto-Reporter")
    print(f"   Interval: {REPORT_INTERVAL_SEC / 3600:.0f}h")
    print(f"   Tracker:  {REPORT_LOG}")
    print("   Press Ctrl+C to stop\n")

    run_insights_and_capture()

    try:
        while True:
            time.sleep(REPORT_INTERVAL_SEC)
            run_insights_and_capture()
    except KeyboardInterrupt:
        print("\nAuto-reporter stopped (Ctrl+C).")
    except Exception as e:
        print(f"\nFatal loop error: {e}")


if __name__ == "__main__":
    main()
