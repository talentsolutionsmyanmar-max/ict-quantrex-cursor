#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd


def print_exit_reason_breakdown(trades_df: pd.DataFrame) -> None:
    if trades_df.empty or "exit_reason" not in trades_df.columns:
        print("EXIT REASON BREAKDOWN: (no exit_reason column or empty)")
        return
    reasons = trades_df["exit_reason"].astype(str).value_counts(normalize=True) * 100.0
    print("EXIT REASON BREAKDOWN:")
    for reason, pct in reasons.items():
        sub = trades_df[trades_df["exit_reason"].astype(str) == str(reason)]
        r_mult_mean = float(sub["r_multiple"].mean()) if "r_multiple" in sub.columns and len(sub) else 0.0
        print(f"   {str(reason):20s} | {float(pct):5.1f}% | Avg R: {r_mult_mean:+.3f}")


def _exit_reason_breakdown_list(trades_df: pd.DataFrame) -> List[Dict[str, Any]]:
    if trades_df.empty or "exit_reason" not in trades_df.columns:
        return []
    reasons = trades_df["exit_reason"].astype(str).value_counts(normalize=True) * 100.0
    out: List[Dict[str, Any]] = []
    for reason, pct in reasons.items():
        sub = trades_df[trades_df["exit_reason"].astype(str) == str(reason)]
        r_mult_mean = float(sub["r_multiple"].mean()) if "r_multiple" in sub.columns and len(sub) else 0.0
        out.append(
            {
                "reason": str(reason),
                "pct": round(float(pct), 2),
                "avg_r_multiple": round(r_mult_mean, 4),
                "count": int(len(sub)),
            }
        )
    return out


def analyze_distribution(
    trades_df: pd.DataFrame,
    output_path: str = "reports/trade_distribution_analysis.json",
    *,
    include_breakdown: bool = False,
) -> Dict[str, Any]:
    wins = trades_df[trades_df["r_multiple"] > 0]
    losses = trades_df[trades_df["r_multiple"] <= 0]

    wr = len(wins) / len(trades_df) if len(trades_df) > 0 else 0
    avg_win = float(wins["r_multiple"].mean()) if len(wins) > 0 else 0.0
    avg_loss = float(abs(losses["r_multiple"].mean())) if len(losses) > 0 else 0.0
    payout_ratio = (avg_win / avg_loss) if avg_loss > 0 else 0.0
    pf = (len(wins) * avg_win) / (len(losses) * avg_loss) if len(losses) > 0 and avg_loss > 0 else 999.0

    ts_col = "timestamp" if "timestamp" in losses.columns else ("exit_time" if "exit_time" in losses.columns else None)
    worst_hour = None
    if ts_col and not losses.empty:
        hours = pd.to_datetime(losses[ts_col], errors="coerce").dt.hour
        loss_by_hour = losses.groupby(hours)["r_multiple"].mean()
        worst_hour = int(loss_by_hour.idxmin()) if not loss_by_hour.empty else None

    reason_col = "exit_reason" if "exit_reason" in trades_df.columns else None
    exit_reason_counts: dict = {}
    if reason_col:
        vc = trades_df[reason_col].astype(str).value_counts()
        exit_reason_counts = {str(k): int(v) for k, v in vc.items()}

    breakdown = _exit_reason_breakdown_list(trades_df) if include_breakdown else []

    report: Dict[str, Any] = {
        "total_trades": int(len(trades_df)),
        "win_rate": round(float(wr), 3),
        "avg_win_r": round(float(avg_win), 3),
        "avg_loss_r": round(float(avg_loss), 3),
        "payout_ratio": round(float(payout_ratio), 3),
        "pf_actual": round(float(pf), 3),
        "expectancy_r": round(float((wr * avg_win) - ((1 - wr) * avg_loss)), 3),
        "worst_loss_hour_utc": worst_hour,
        "exit_reason_counts": exit_reason_counts,
        "pf_leak_diagnosis": (
            "TIGHT_TRAIL"
            if avg_win < 1.3 and payout_ratio < 1.5
            else "LOSS_TAIL"
            if avg_loss > 0.9 and wr > 0.5
            else "ENTRY_NOISE"
            if wr < 0.45
            else "OPTIMAL"
        ),
    }
    if include_breakdown:
        report["exit_reason_breakdown"] = breakdown

    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    if include_breakdown:
        print_exit_reason_breakdown(trades_df)
    return report


def _load_input(path: Path) -> pd.DataFrame:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return pd.DataFrame(payload)
    if isinstance(payload, dict) and "trades" in payload and isinstance(payload["trades"], list):
        return pd.DataFrame(payload["trades"])
    raise ValueError("Input JSON must be a trade array or {'trades': [...]} object")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", default="reports/trade_distribution_analysis.json")
    parser.add_argument("--breakdown", action="store_true", help="Print exit_reason pareto lines")
    args = parser.parse_args()

    df = _load_input(Path(args.input))
    if df.empty:
        print('{"error":"input trades are empty"}')
        sys.exit(1)
    analyze_distribution(df, output_path=args.output, include_breakdown=bool(args.breakdown))
