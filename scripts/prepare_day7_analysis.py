#!/usr/bin/env python3
"""
v2.7.7 — Day-7 merged preview (read-only). Writes reports/day7_merged_preview.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DOM_LOG = ROOT / "data" / "dom_cvd_audit.jsonl"
AUDIT_LOG = ROOT / "data" / "signal_audit.jsonl"
OUTPUT = ROOT / "reports" / "day7_merged_preview.json"


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


def prepare_preview():
    if not DOM_LOG.exists() or not AUDIT_LOG.exists():
        print("Logs not ready yet (missing dom_cvd_audit or signal_audit).")
        return None

    dom_df = pd.DataFrame(_load_jsonl(DOM_LOG))
    audit_df = pd.DataFrame(_load_jsonl(AUDIT_LOG))
    if dom_df.empty or audit_df.empty:
        print("Logs empty.")
        return None

    dom_df["ts"] = pd.to_datetime(dom_df["ts"], utc=True, errors="coerce")
    audit_df["ts"] = pd.to_datetime(audit_df["ts"], utc=True, errors="coerce")
    dom_df = dom_df.dropna(subset=["ts"]).sort_values("ts")
    audit_df = audit_df.dropna(subset=["ts"]).sort_values("ts")

    merged = pd.merge_asof(
        audit_df,
        dom_df,
        on="ts",
        direction="nearest",
        tolerance=pd.Timedelta("15m"),
        suffixes=("_audit", "_dom"),
    )

    imb_col = "dom_imbalance_dom" if "dom_imbalance_dom" in merged.columns else "dom_imbalance"
    merged_imb = merged[imb_col] if imb_col in merged.columns else pd.Series(dtype=float)

    preview = {
        "total_signal_rows": int(len(audit_df)),
        "total_dom_rows": int(len(dom_df)),
        "merged_samples": int(len(merged)),
        "merged_with_imbalance": int(merged_imb.notna().sum()),
        "regime_counts": {str(k): int(v) for k, v in merged["regime"].value_counts().items()}
        if "regime" in merged.columns
        else {},
        "enter_samples": int(len(merged[merged["decision"] == "ENTER"]))
        if "decision" in merged.columns
        else 0,
        "skip_samples": int(len(merged[merged["decision"] == "SKIP"]))
        if "decision" in merged.columns
        else 0,
        "avg_imbalance_by_regime": {},
        "note": "PREVIEW ONLY — Do not draw conclusions until Day-7 gate passes",
    }

    if imb_col in merged.columns and "regime" in merged.columns:
        preview["avg_imbalance_by_regime"] = (
            merged.groupby("regime")[imb_col].mean().round(4).to_dict()
        )
        preview["avg_imbalance_by_regime"] = {str(k): float(v) for k, v in preview["avg_imbalance_by_regime"].items()}

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(preview, indent=2, default=str), encoding="utf-8")
    print(f"Day-7 merged preview prepared: {OUTPUT}")
    return preview


if __name__ == "__main__":
    prepare_preview()
