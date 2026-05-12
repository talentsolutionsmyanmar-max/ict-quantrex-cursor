#!/usr/bin/env python3
"""
v2.7.7 — Daily observation snapshot (read-only). Writes reports/daily_observation.json
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REPORT = ROOT / "reports" / "daily_observation.json"
PREFLIGHT = ROOT / "reports" / "dom_cvd_day7_preflight.json"


def _python_cmd_blob() -> str:
    blobs: list[str] = []
    for exe in ("python.exe", "pythonw.exe"):
        try:
            r = subprocess.run(
                ["wmic", "process", "where", f"name='{exe}'", "get", "commandline"],
                capture_output=True,
                text=True,
                timeout=45,
                encoding="utf-8",
                errors="ignore",
            )
            blobs.append((r.stdout or "") + (r.stderr or ""))
        except Exception:
            continue
    return "\n".join(blobs).lower()


def _process_running(needles: list[str]) -> bool:
    hay = _python_cmd_blob()
    return all(n.lower() in hay for n in needles)


def _rows_last_24h(path: Path) -> int:
    import pandas as pd

    if not path.exists():
        return 0
    cut = pd.Timestamp.now(tz="UTC") - pd.Timedelta(hours=24)
    n = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            ts = row.get("ts")
            if not ts:
                continue
            t = pd.to_datetime(ts, utc=True, errors="coerce")
            if pd.isna(t):
                continue
            if t >= cut:
                n += 1
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
    return n


def run_check():
    checks: dict = {}

    checks["paper_loop_running"] = _process_running(
        ["paper_trader.py", "--live", "config/v2.6_live_micro.yaml"]
    )
    checks["dom_collector_running"] = _process_running(
        ["dom_cvd_collector.py", "--interval", "900"]
    )

    checks["signal_last_24h_rows"] = _rows_last_24h(ROOT / "data" / "signal_audit.jsonl")
    checks["dom_last_24h_rows"] = _rows_last_24h(ROOT / "data" / "dom_cvd_audit.jsonl")

    if PREFLIGHT.exists():
        pre = json.loads(PREFLIGHT.read_text(encoding="utf-8"))
        checks["preflight_snapshot"] = {
            "ready_for_day7": pre.get("ready_for_day7"),
            "dom_audit_lines": pre.get("dom_audit_lines"),
            "signal_audit_lines": pre.get("signal_audit_lines"),
            "merged_with_dom_imbalance": pre.get("merged_with_dom_imbalance"),
            "checked_at": pre.get("checked_at"),
        }

    checks["ready_for_day7"] = (
        checks.get("preflight_snapshot") or {}
    ).get("ready_for_day7", False)

    checks["checked_at"] = datetime.now(timezone.utc).isoformat()

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(checks, indent=2, default=str), encoding="utf-8")

    print("DAILY OBSERVATION CHECK")
    print("=" * 40)
    print(f"Paper loop running: {checks.get('paper_loop_running')}")
    print(f"DOM collector running: {checks.get('dom_collector_running')}")
    print(f"Signal rows (24h): {checks.get('signal_last_24h_rows')} (target: ~96)")
    print(f"DOM rows (24h): {checks.get('dom_last_24h_rows')} (target: ~96)")
    print(f"Day-7 ready (from last preflight): {checks.get('ready_for_day7')}")
    print(f"Report saved: {REPORT}")
    print("=" * 40)

    return checks


if __name__ == "__main__":
    run_check()
