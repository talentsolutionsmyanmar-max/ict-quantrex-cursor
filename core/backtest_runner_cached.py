"""
Fast backtest using offline klines (parquet/pickle) via BINANCE_KLINES_PARQUET.
No network during run() if env points at a file.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backtester import Backtester
from config import build_config
from ict_engine import ICTEngine


def load_configured_ohlcv() -> pd.DataFrame:
    cfg = build_config()
    p = os.environ.get("BINANCE_KLINES_PARQUET", "").strip()
    if not p or not Path(p).is_file():
        print("ERROR: Set BINANCE_KLINES_PARQUET to a .parquet or .pkl from scripts/cache_klines.py")
        sys.exit(1)
    os.environ["BINANCE_KLINES_PARQUET"] = p
    from data_handler import DataHandler

    return DataHandler(cfg).fetch_historical_data(cfg.BACKTEST_START_DATE, cfg.BACKTEST_END_DATE)


def run_ict_and_cache(cfg=None) -> pd.DataFrame:
    cfg = cfg or build_config()
    df = load_configured_ohlcv()
    return ICTEngine(cfg).process_dataframe(df)


def run_fast_trend_down_sweep_row(
    processed_df: pd.DataFrame,
    trend_down_exits: Dict[str, Any],
) -> Dict[str, Any]:
    """Single sweep row: re-sim on same ICT frame with trend_down exit overrides."""
    cfg = build_config()
    bt = Backtester(cfg, record_playbook=False, sweep_trend_down_exits=trend_down_exits)
    return bt.backtest_on_processed(processed_df, verbose=False)


def prepare_processed_frame() -> pd.DataFrame:
    return run_ict_and_cache()


def prepare_sweep_window(sweep_days: int) -> pd.DataFrame:
    """ICT on the last ``sweep_days`` of cached OHLCV only (fast sweeps)."""
    cfg = build_config()
    df = load_configured_ohlcv()
    ts = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    cutoff = ts.max() - pd.Timedelta(days=int(sweep_days))
    mask = ts >= cutoff
    df = df.loc[mask].copy()
    if df.empty:
        print("ERROR: sweep window produced empty dataframe")
        sys.exit(1)
    return ICTEngine(cfg).process_dataframe(df)


class FastBacktester:
    """Checkpoint-friendly cached runner used by resilient sweep scripts."""

    def __init__(self, kline_path: str):
        p = Path(kline_path)
        if not p.is_file():
            print(f"ERROR: cache file not found: {kline_path}")
            sys.exit(1)
        self.kline_path = str(p.resolve())
        os.environ["BINANCE_KLINES_PARQUET"] = self.kline_path
        self._processed_full: Optional[pd.DataFrame] = None
        self._processed_by_days: Dict[int, pd.DataFrame] = {}

    def _processed_window(self, days: int) -> pd.DataFrame:
        d = int(days)
        if d in self._processed_by_days:
            return self._processed_by_days[d]
        processed = prepare_sweep_window(d)
        self._processed_by_days[d] = processed
        return processed

    def run_fast(self, config: Dict[str, Any], days: int = 180) -> pd.DataFrame:
        td = ((config or {}).get("exits") or {}).get("trend_down") or {}
        out = run_fast_trend_down_sweep_row(
            self._processed_window(int(days)),
            trend_down_exits=dict(td),
        )
        trades = out.get("trades", [])
        df = pd.DataFrame(trades)
        if df.empty or "entry_regime_state" not in df.columns:
            return df
        return df[df["entry_regime_state"].astype(str) == "trend_down"].copy()
