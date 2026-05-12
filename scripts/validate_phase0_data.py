#!/usr/bin/env python3
"""
Dry-run Phase 0 data pipeline: fetch aligned OHLCV (+ optional gated sources).

Usage:
  python scripts/validate_phase0_data.py --symbol BTCUSDT --days 1
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data.pipeline import CryptoNativeDataPipeline  # noqa: E402
from strategy.load_spec import read_raw_spec  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate Phase 0 CryptoNativeDataPipeline (read-only).")
    ap.add_argument("--symbol", default="BTCUSDT", help="Binance REST symbol, e.g. BTCUSDT")
    ap.add_argument("--days", type=int, default=1, help="Lookback in days")
    ap.add_argument("--spec", type=str, default=str(ROOT / "strategy" / "spec.yaml"), help="Path to spec.yaml")
    args = ap.parse_args()

    raw = read_raw_spec(Path(args.spec))
    gates = raw.get("adaptation_gates") or {}
    if not gates.get("phase_0_only", True):
        print("WARNING: adaptation_gates.phase_0_only is not true — review before enabling providers.")

    pipe = CryptoNativeDataPipeline(spec=raw)
    end = datetime.now(timezone.utc)
    lookback_h = max(6, int(args.days) * 24)
    try:
        df = pipe.fetch_aligned(args.symbol, end, lookback_hours=lookback_h)
    except Exception as e:
        print(f"FETCH_FAILED: {e}")
        return 1

    if df.empty:
        print("FAIL: empty dataframe")
        return 1

    crit = ("open", "high", "low", "close", "volume")
    if any(df[c].isna().any() for c in crit):
        print("FAIL: NaN in OHLCV core columns")
        return 1

    ts = df["timestamp"].sort_values()
    gaps = int((ts.diff() > pd.Timedelta(minutes=20)).sum()) if len(ts) > 1 else 0
    gap_rate_pct = 100.0 * gaps / max(len(df), 1)

    freshness_s = (datetime.now(timezone.utc) - pd.to_datetime(df["timestamp"].max(), utc=True)).total_seconds()

    print("PHASE0_VALIDATION_REPORT")
    print(f"  symbol={args.symbol} rows={len(df)} lookback_hours={lookback_h}")
    print(f"  ts_min={df['timestamp'].min()} ts_max={df['timestamp'].max()}")
    print(f"  alignment_gap_count={gaps} alignment_gap_rate_pct={gap_rate_pct:.2f}")
    print(f"  ohlcv_freshness_seconds={freshness_s:.1f}")
    aux = [c for c in df.columns if c not in crit and c != "timestamp" and not str(c).startswith("close_time")]
    print(f"  aux_columns={aux}")
    if gap_rate_pct > 3.0:
        print("WARN: alignment_gap_rate_pct exceeds 3% target (grid may be sparse or exchange gaps)")
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
