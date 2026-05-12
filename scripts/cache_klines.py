#!/usr/bin/env python3
"""
One-time (or refresh) download of historical klines to local parquet/pickle.
Set BINANCE_KLINES_PARQUET to the output path for zero-network backtests/sweeps.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import build_config
from data_handler import DataHandler


def cache_btc_15m(
    start: str = "2024-01-01",
    end: str = "2026-04-24",
    save_path: str = "data/klines_cache/btcusdt_15m.parquet",
) -> None:
    out = Path(save_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    cfg = build_config()
    cfg.SYMBOL = "BTCUSDT"
    cfg.TIMEFRAME = "15m"
    cfg.BACKTEST_START_DATE = start
    cfg.BACKTEST_END_DATE = end
    print(f"Fetching {cfg.SYMBOL} {cfg.TIMEFRAME} {start}..{end} (one-time or refresh)...")
    df = DataHandler(cfg).fetch_historical_data(start, end)
    try:
        df.to_parquet(out, index=True)
    except Exception as e:
        pkl = out.with_suffix(".pkl")
        print(f"parquet failed ({e}); writing pickle to {pkl}")
        df.to_pickle(pkl)
        out = pkl
    print(f"OK Cached {len(df)} rows -> {out.resolve()}")
    print(f"   set BINANCE_KLINES_PARQUET={out.resolve()}")


if __name__ == "__main__":
    cache_btc_15m()
