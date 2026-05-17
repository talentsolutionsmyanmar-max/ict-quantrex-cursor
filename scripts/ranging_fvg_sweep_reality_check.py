#!/usr/bin/env python3
"""48h ranging-regime FVG/sweep breakdown from data/signal_audit.jsonl."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "data" / "signal_audit.jsonl"


def _parse_ts(raw: str) -> datetime:
    return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))


def run(*, hours: int = 48, audit_path: Path = AUDIT) -> int:
    if not audit_path.is_file():
        print(f"Missing audit log: {audit_path}")
        return 1

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    counts: Counter = Counter()
    rows_in_window = 0

    with audit_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            ts_raw = d.get("ts")
            if not ts_raw:
                continue
            ts = _parse_ts(ts_raw)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts <= cutoff:
                continue
            rows_in_window += 1
            if d.get("regime") != "ranging":
                continue
            counts["total_ranging"] += 1
            if d.get("skip_reason") != "no_signal":
                continue
            fvg = bool(d.get("fvg_detected"))
            sweep = bool(d.get("sweep_detected"))
            if fvg or sweep:
                counts["pattern_seen_but_rejected"] += 1
            else:
                counts["no_pattern_formed"] += 1

    print(f"48h Ranging Regime Breakdown ({hours}h window, audit={audit_path.name}):")
    print(f"  rows_in_window_all_regimes: {rows_in_window}")
    for k in ("total_ranging", "no_pattern_formed", "pattern_seen_but_rejected"):
        print(f"  {k}: {counts[k]}")
    if counts["total_ranging"] == 0:
        print("  -> No ranging rows in window (stale audit or bot not logging).")
        return 2
    if counts["total_ranging"] > 0 and counts["no_pattern_formed"] + counts["pattern_seen_but_rejected"] == 0:
        print("  -> no_signal rows lack fvg_detected/sweep_detected; deploy latest audit writer.")
        return 3
    pct = counts["no_pattern_formed"] / counts["total_ranging"] * 100
    print(f"  -> {pct:.1f}% of ranging no_signal bars had NO FVG/sweep patterns")
    if pct > 90:
        print("  CALL: pause calibration — fix Layer 1 detection first.")
    elif pct >= 50:
        print("  CALL: consider Lever 6 (min_gap_atr 0.15 -> 0.12), one lever only.")
    else:
        print("  CALL: debug sweep/FVG rejection path (gates), not gap tuning.")
    return 0


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--hours", type=int, default=48)
    p.add_argument("--audit", type=Path, default=AUDIT)
    args = p.parse_args()
    raise SystemExit(run(hours=args.hours, audit_path=args.audit))


if __name__ == "__main__":
    main()
