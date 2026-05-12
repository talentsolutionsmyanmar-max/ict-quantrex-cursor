#!/usr/bin/env python3
"""
v2.7.1 — Read-only DOM/CVD partial insights from local JSONL logs.
Does not modify config or live stack. Safe to run anytime.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DOM_LOG = ROOT / "data" / "dom_cvd_audit.jsonl"
AUDIT_LOG = ROOT / "data" / "signal_audit.jsonl"
OUTPUT_TXT = ROOT / "reports" / "dom_cvd_partial_insights.txt"
OUTPUT_JSON = ROOT / "reports" / "dom_cvd_partial_insights.json"


def load_logs():
    """Load logs with graceful fallback for partial/empty data."""
    dom_data, audit_data = [], []

    if DOM_LOG.exists() and DOM_LOG.stat().st_size > 0:
        with open(DOM_LOG, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    try:
                        dom_data.append(json.loads(line))
                    except Exception:
                        pass

    if AUDIT_LOG.exists() and AUDIT_LOG.stat().st_size > 0:
        with open(AUDIT_LOG, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    try:
                        audit_data.append(json.loads(line))
                    except Exception:
                        pass

    dom_df = pd.DataFrame(dom_data) if dom_data else pd.DataFrame()
    audit_df = pd.DataFrame(audit_data) if audit_data else pd.DataFrame()
    return dom_df, audit_df


def _as_wall_list(val) -> list | None:
    """Normalize merged cell values (list or ndarray) to list of dicts."""
    if val is None:
        return None
    if isinstance(val, (float, np.floating)) and pd.isna(val):
        return None
    if isinstance(val, np.ndarray):
        val = val.tolist()
    if isinstance(val, list):
        return val
    return None


def near_wall(row: pd.Series) -> bool:
    walls = _as_wall_list(row.get("dom_walls"))
    if walls is None:
        return False
    price = row.get("price")
    if pd.isna(price):
        return False
    for w in walls:
        if not isinstance(w, dict):
            continue
        try:
            dist = abs(float(w["price"]) - float(price)) / float(price)
            if dist < 0.01:
                return True
        except (TypeError, ValueError, KeyError):
            continue
    return False


def generate_partial_insights():
    dom_df, audit_df = load_logs()
    merged = pd.DataFrame()
    merged_imb_col: str | None = None
    wall_skip_rate: float | None = None

    lines: list[str] = []
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines.append("QUANTREX DOM/CVD PARTIAL INSIGHTS")
    lines.append(f"Generated: {now_utc}")
    lines.append(f"DOM snapshots: {len(dom_df)} | Signal evaluations: {len(audit_df)}")
    lines.append("=" * 60 + "\n")

    # 1. DATA COVERAGE SUMMARY
    if not dom_df.empty and "ts" in dom_df.columns:
        dom_df = dom_df.copy()
        dom_df["ts"] = pd.to_datetime(dom_df["ts"], utc=True, errors="coerce")
        dom_df = dom_df.dropna(subset=["ts"])
        if len(dom_df) >= 2:
            time_span_h = (dom_df["ts"].max() - dom_df["ts"].min()).total_seconds() / 3600
            lines.append(f"DOM Data Coverage: {time_span_h:.1f} hours ({len(dom_df)} snapshots)")
            avg_min = time_span_h * 60 / max(len(dom_df), 1)
            lines.append(f"   Avg interval: {avg_min:.1f} min (target: 15 min)\n")
        elif len(dom_df) == 1:
            lines.append(f"DOM Data Coverage: single snapshot ({len(dom_df)} row)\n")
    elif dom_df.empty:
        lines.append("No DOM data yet. Optional: python core/dom_cvd_collector.py (when available)\n")

    # 2. LIVE DOM SNAPSHOT (Most Recent)
    if not dom_df.empty:
        latest = dom_df.iloc[-1]
        lines.append("LATEST DOM SNAPSHOT")
        p = latest.get("price", "N/A")
        if isinstance(p, (int, float)):
            lines.append(f"   Price: ${float(p):,.2f}")
        else:
            lines.append(f"   Price: {p}")
        imb = float(latest.get("dom_imbalance", 0) or 0)
        tag = "BULL" if imb > 0.1 else "BEAR" if imb < -0.1 else "NEUTRAL"
        lines.append(f"   Imbalance: {imb:+.4f} ({tag})")
        lines.append(f"   CVD Cumulative: {float(latest.get('cvd_cumulative', 0) or 0):+.2f}")
        walls = latest.get("dom_walls", [])
        if not isinstance(walls, list):
            walls = []
        lines.append(f"   Walls Detected: {len(walls)}")
        for w in walls[:3]:
            if isinstance(w, dict):
                try:
                    lines.append(
                        f"      - {str(w.get('side', '')).upper()} wall @ ${float(w['price']):,.2f} (vol: {float(w.get('vol', 0)):.2f})"
                    )
                except (TypeError, ValueError, KeyError):
                    pass
        lines.append("")

    # 3–5. Merge audit + DOM
    if not audit_df.empty and not dom_df.empty and "ts" in audit_df.columns:
        audit_m = audit_df.copy()
        audit_m["ts"] = pd.to_datetime(audit_m["ts"], utc=True, errors="coerce")
        audit_m = audit_m.dropna(subset=["ts"]).sort_values("ts")
        dom_m = dom_df.copy()
        if "ts" not in dom_m.columns:
            dom_m["ts"] = pd.NaT
        dom_m["ts"] = pd.to_datetime(dom_m["ts"], utc=True, errors="coerce")
        dom_m = dom_m.dropna(subset=["ts"]).sort_values("ts")

        if not audit_m.empty and not dom_m.empty:
            merged = pd.merge_asof(
                audit_m,
                dom_m,
                on="ts",
                direction="nearest",
                tolerance=pd.Timedelta("15m"),
                suffixes=("_audit", "_dom"),
            )
            imb_col = "dom_imbalance_dom" if "dom_imbalance_dom" in merged.columns else "dom_imbalance"
            cvd_col = "cvd_delta_dom" if "cvd_delta_dom" in merged.columns else "cvd_delta"
            merged_imb_col = imb_col if imb_col in merged.columns else None
            if imb_col in merged.columns and merged[imb_col].isna().all():
                lines.append(
                    "NOTE: No audit rows within 15m of DOM snapshots (stale audit or clock skew). "
                    "Regime merge stats skipped; collect overlapping audit + DOM windows for alignment.\n"
                )

            lines.append("DOM BY REGIME (Merged with Signal Audit)")
            if "regime" in merged.columns and imb_col in merged.columns and not merged[imb_col].isna().all():
                for regime in merged["regime"].dropna().unique():
                    s = merged.loc[merged["regime"] == regime, imb_col].dropna()
                    if s.empty:
                        continue
                    std_v = float(s.std()) if len(s) > 1 else 0.0
                    lines.append(
                        f"   {str(regime):12s} | Imb: {float(s.mean()):+.4f} +/-{std_v:.4f} | n={len(s)}"
                    )
            elif "regime" in merged.columns:
                lines.append("   (no overlapping DOM within 15m tolerance)\n")
            lines.append("")

            lines.append("DOM AT SIGNAL MOMENTS")
            if "decision" in merged.columns and imb_col in merged.columns:
                for decision in ["ENTER", "SKIP"]:
                    subset = merged[merged["decision"] == decision]
                    if subset.empty:
                        continue
                    sub_imb = subset[imb_col].dropna()
                    if sub_imb.empty:
                        continue
                    avg_imb = float(sub_imb.mean())
                    if cvd_col in subset.columns:
                        avg_cvd = float(subset[cvd_col].dropna().mean()) if not subset[cvd_col].dropna().empty else 0.0
                    else:
                        avg_cvd = 0.0
                    lines.append(
                        f"   {decision:6s} | Avg Imb: {avg_imb:+.4f} | Avg CVDd: {avg_cvd:+.2f} | n={len(subset)}"
                    )
            lines.append("")

            # Wall proximity: use dom side columns after merge
            wall_col = "dom_walls_dom" if "dom_walls_dom" in merged.columns else "dom_walls"
            price_col = "price_dom" if "price_dom" in merged.columns else "price"
            if wall_col in merged.columns and price_col in merged.columns:
                def _nw(row):
                    walls = _as_wall_list(row.get(wall_col))
                    price = row.get(price_col)
                    if walls is None:
                        return False
                    if pd.isna(price):
                        return False
                    for w in walls:
                        if not isinstance(w, dict):
                            continue
                        try:
                            if abs(float(w["price"]) - float(price)) / float(price) < 0.01:
                                return True
                        except (TypeError, ValueError, KeyError):
                            continue
                    return False

                merged["near_wall"] = merged.apply(_nw, axis=1)
            else:
                merged["near_wall"] = merged.apply(near_wall, axis=1)

            if "near_wall" in merged.columns and "decision" in merged.columns:
                nw = merged[merged["near_wall"]]
                if not nw.empty:
                    skips = nw[nw["decision"] == "SKIP"]
                    wall_skip_rate = len(skips) / max(1, len(nw))
                    lines.append("WALL PROXIMITY FILTER")
                    lines.append(f"   When wall <1% away: SKIP rate = {wall_skip_rate:.1%}")
                    lines.append("   -> Walls may be a valid confluence filter (validate with more data)\n")

    # 6. PRELIMINARY RECOMMENDATIONS
    lines.append("PRELIMINARY INSIGHTS (Partial Data)")
    if not dom_df.empty and "dom_imbalance" in dom_df.columns:
        avg_imb = float(dom_df["dom_imbalance"].astype(float).mean())
        if abs(avg_imb) < 0.05:
            lines.append("   - Market showing balanced order flow (imbalance near zero)")
            lines.append("   - Wait for imbalance > |0.15| before treating DOM as strong confluence")
        elif avg_imb > 0.15:
            lines.append("   - Persistent bid pressure detected (imbalance > +0.15)")
            lines.append("   - Consider weighting LONG signals slightly higher in confluence (post-lock review)")
        elif avg_imb < -0.15:
            lines.append("   - Persistent ask pressure detected (imbalance < -0.15)")
            lines.append("   - Consider weighting SHORT signals slightly higher in confluence (post-lock review)")
        else:
            lines.append("   - Transitional / mixed DOM (avg imbalance between neutral and strong thresholds)")

        if len(dom_df) > 10 and "cvd_cumulative" in dom_df.columns:
            cvd = dom_df["cvd_cumulative"].astype(float)
            recent_cvd = float(cvd.iloc[-1] - cvd.iloc[-10])
            if recent_cvd > 50:
                lines.append("   - CVD rising: aggressive buying pressure building (recent window)")
            elif recent_cvd < -50:
                lines.append("   - CVD falling: aggressive selling pressure building (recent window)")

    lines.append("\n" + "=" * 60)
    lines.append("GOLDEN LOCK STATUS: v2.6.5 config UNCHANGED (read-only script)")
    lines.append("This is read-only analysis. No live system modifications.")
    lines.append(f"\nFull JSON output: {OUTPUT_JSON}")

    OUTPUT_TXT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_TXT.write_text("\n".join(lines), encoding="utf-8")

    # JSON report
    json_report: dict = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dom_snapshots": int(len(dom_df)),
        "signal_evaluations": int(len(audit_df)),
        "latest_dom": dom_df.iloc[-1].to_dict() if not dom_df.empty else None,
        "regime_imbalance": {},
        "signal_dom_correlation": {
            "enter_avg_imbalance": None,
            "skip_avg_imbalance": None,
        },
        "wall_filter_effectiveness": wall_skip_rate,
        "preliminary_recommendations": [ln.strip(" - ").strip() for ln in lines if ln.strip().startswith("   - ")],
    }

    if (
        not merged.empty
        and merged_imb_col
        and merged[merged_imb_col].notna().any()
        and "regime" in merged.columns
    ):
        json_report["regime_imbalance"] = merged.groupby("regime")[merged_imb_col].mean().to_dict()
    if not merged.empty and merged_imb_col and "decision" in merged.columns:
        ent = merged[merged["decision"] == "ENTER"]
        skp = merged[merged["decision"] == "SKIP"]
        if not ent.empty and not ent[merged_imb_col].dropna().empty:
            json_report["signal_dom_correlation"]["enter_avg_imbalance"] = float(ent[merged_imb_col].mean())
        if not skp.empty and not skp[merged_imb_col].dropna().empty:
            json_report["signal_dom_correlation"]["skip_avg_imbalance"] = float(skp[merged_imb_col].mean())

    OUTPUT_JSON.write_text(json.dumps(json_report, indent=2, default=str), encoding="utf-8")

    print("\n".join(lines))
    return json_report


if __name__ == "__main__":
    generate_partial_insights()
