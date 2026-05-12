#!/usr/bin/env python3
"""
v2.8.4 — Autonomous daily health run (read-only). Chdir to project root, run observation
scripts, verify dashboard /api/health, write reports/autonomous_run_summary.json.
"""
from __future__ import annotations

import json
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


def find_project_root() -> Path | None:
    here = Path(__file__).resolve()
    candidates = [
        here.parent.parent,
        Path(r"C:\Users\the RECRUITER\Documents\Claude-Claw\ict-quantrex-cursor"),
        Path.cwd(),
    ]
    for p in candidates:
        try:
            p = p.resolve()
        except OSError:
            continue
        if (p / "config" / "v2.6_live_micro.yaml").exists():
            return p
    return None


def main() -> int:
    project_root = find_project_root()
    if not project_root:
        print("Could not auto-detect project root. Run from ict-quantrex-cursor or set path.")
        return 1

    import os

    os.chdir(project_root)
    print(f"Working directory: {project_root}")

    required = [
        "scripts/daily_observation_check.py",
        "scripts/check_multi_dom_growth.py",
        "config/v2.6_live_micro.yaml",
    ]
    for f in required:
        if not (project_root / f).exists():
            print(f"Missing: {f}")
            return 1
    print("All required files present")

    print("\nRUNNING: daily_observation_check.py")
    r1 = subprocess.run(
        [sys.executable, "scripts/daily_observation_check.py"],
        capture_output=True,
        text=True,
        cwd=str(project_root),
        encoding="utf-8",
        errors="replace",
    )
    print(r1.stdout or "")
    if r1.stderr:
        print(f"stderr: {r1.stderr[:500]}")

    print("\nRUNNING: check_multi_dom_growth.py")
    r2 = subprocess.run(
        [sys.executable, "scripts/check_multi_dom_growth.py"],
        capture_output=True,
        text=True,
        cwd=str(project_root),
        encoding="utf-8",
        errors="replace",
    )
    print(r2.stdout or "")
    if r2.stderr:
        print(f"stderr: {r2.stderr[:500]}")

    print("\nVERIFYING: Dashboard health (port 5050)")
    dashboard_ok = False
    dashboard_detail = ""
    try:
        req = urllib.request.Request("http://127.0.0.1:5050/api/health", method="GET")
        with urllib.request.urlopen(req, timeout=10) as resp:
            dashboard_ok = resp.status == 200
            dashboard_detail = f"HTTP {resp.status} - Dashboard responsive"
            print(f"OK {dashboard_detail}")
    except urllib.error.HTTPError as e:
        dashboard_detail = f"HTTPError {e.code}: {e.reason}"
        print(f"Dashboard check failed: {dashboard_detail}")
    except Exception as e:
        dashboard_detail = str(e)
        print(f"Dashboard check failed: {e}")

    summary = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "project_root": str(project_root),
        "checks_run": [
            "daily_observation_check.py",
            "check_multi_dom_growth.py",
            "dashboard_health_5050",
        ],
        "golden_lock_status": "INTACT - no config edits performed",
        "next_action": "Accumulation continues. Return outputs to Senior Quant Engineer.",
        "subprocess_exit_codes": {
            "daily_observation_check": r1.returncode,
            "check_multi_dom_growth": r2.returncode,
        },
        "dashboard_http_200": dashboard_ok,
        "dashboard_detail": dashboard_detail,
    }

    print("\n" + "=" * 60)
    print("EXECUTION SUMMARY")
    print("=" * 60)
    for k, v in summary.items():
        print(f"{k:22s}: {v}")
    print("=" * 60)

    summary_path = project_root / "reports" / "autonomous_run_summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"Summary saved: {summary_path}")

    if r1.returncode != 0 or r2.returncode != 0:
        return 1
    return 0 if dashboard_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
