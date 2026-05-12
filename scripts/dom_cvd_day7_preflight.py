#!/usr/bin/env python3
"""
v2.7.4 — Day-7 pre-flight check for DOM/CVD + signal audit (read-only).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DOM_LOG = ROOT / "data" / "dom_cvd_audit.jsonl"
AUDIT_LOG = ROOT / "data" / "signal_audit.jsonl"
TRACKER_LOG = ROOT / "reports" / "dom_cvd_4h_tracker.jsonl"
OUTPUT = ROOT / "reports" / "dom_cvd_day7_preflight.json"


def _line_count(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for _ in path.open(encoding="utf-8") if _.strip())


def _load_jsonl(path: Path) -> list[dict]:
    rows = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return rows


def validate_readiness():
    checks: dict = {}

    checks["dom_audit_exists"] = DOM_LOG.exists()
    checks["dom_audit_lines"] = _line_count(DOM_LOG)
    checks["signal_audit_exists"] = AUDIT_LOG.exists()
    checks["signal_audit_lines"] = _line_count(AUDIT_LOG)
    checks["tracker_exists"] = TRACKER_LOG.exists()
    checks["tracker_lines"] = _line_count(TRACKER_LOG)

    # Time span coverage
    dom_rows = _load_jsonl(DOM_LOG)
    if len(dom_rows) > 1:
        t0 = pd.to_datetime(dom_rows[0]["ts"], utc=True, errors="coerce")
        t1 = pd.to_datetime(dom_rows[-1]["ts"], utc=True, errors="coerce")
        checks["dom_time_span_hours"] = round(
            (t1 - t0).total_seconds() / 3600, 1
        ) if pd.notna(t0) and pd.notna(t1) else 0.0
    else:
        checks["dom_time_span_hours"] = 0.0

    audit_rows = _load_jsonl(AUDIT_LOG)
    if len(audit_rows) > 1:
        t0 = pd.to_datetime(audit_rows[0]["ts"], utc=True, errors="coerce")
        t1 = pd.to_datetime(audit_rows[-1]["ts"], utc=True, errors="coerce")
        checks["signal_time_span_hours"] = round(
            (t1 - t0).total_seconds() / 3600, 1
        ) if pd.notna(t0) and pd.notna(t1) else 0.0
    else:
        checks["signal_time_span_hours"] = 0.0

    # Merge readiness (15m tolerance, same as partial insights)
    checks["merged_samples"] = 0
    checks["merged_with_dom_imbalance"] = 0
    checks["regime_distribution"] = {}
    if dom_rows and audit_rows:
        dom_df = pd.DataFrame(dom_rows)
        audit_df = pd.DataFrame(audit_rows)
        if "ts" in dom_df.columns and "ts" in audit_df.columns:
            dom_df = dom_df.copy()
            audit_df = audit_df.copy()
            dom_df["ts"] = pd.to_datetime(dom_df["ts"], utc=True, errors="coerce")
            audit_df["ts"] = pd.to_datetime(audit_df["ts"], utc=True, errors="coerce")
            dom_df = dom_df.dropna(subset=["ts"]).sort_values("ts")
            audit_df = audit_df.dropna(subset=["ts"]).sort_values("ts")
            if not dom_df.empty and not audit_df.empty:
                merged = pd.merge_asof(
                    audit_df,
                    dom_df,
                    on="ts",
                    direction="nearest",
                    tolerance=pd.Timedelta("15m"),
                    suffixes=("_audit", "_dom"),
                )
                checks["merged_samples"] = int(len(merged))
                imb_col = "dom_imbalance_dom" if "dom_imbalance_dom" in merged.columns else "dom_imbalance"
                if imb_col in merged.columns:
                    checks["merged_with_dom_imbalance"] = int(merged[imb_col].notna().sum())
                if "regime" in merged.columns:
                    checks["regime_distribution"] = merged["regime"].value_counts().to_dict()
                    # JSON keys as strings
                    checks["regime_distribution"] = {str(k): int(v) for k, v in checks["regime_distribution"].items()}

    # Day-7 gate (tunable heuristics; DOM at 4/h * 7d = 672, signal 4/h * 7d = 672 for 15m evals)
    reg_n = len(checks.get("regime_distribution") or {})
    checks["ready_for_day7"] = bool(
        checks["dom_audit_lines"] >= 670
        and checks["signal_audit_lines"] >= 670
        and checks["merged_with_dom_imbalance"] >= 30
        and reg_n >= 2
    )

    checks["checked_at"] = datetime.now(timezone.utc).isoformat()

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(checks, indent=2), encoding="utf-8")

    print("DAY-7 PRE-FLIGHT CHECK")
    print("=" * 40)
    for k, v in checks.items():
        print(f"{k:32s}: {v}")
    print("=" * 40)
    print("Saved:", OUTPUT)
    return checks


if __name__ == "__main__":
    validate_readiness()
