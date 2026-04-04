"""
Disk cache for Binance historical klines (symbol + interval + date range).

Speeds up evolution and repeated backtests. Set DISABLE_KLINES_CACHE=1 to force live fetch.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

_CACHE_ROOT = Path(__file__).resolve().parent / "data" / "klines_cache"


def _safe_segment(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s)


def cache_path(*, symbol: str, interval: str, start_date: str, end_date: str) -> Path:
    sym = _safe_segment(symbol.upper().replace("/", ""))
    tf = _safe_segment(interval)
    sd = _safe_segment(start_date)
    ed = _safe_segment(end_date)
    return _CACHE_ROOT / f"{sym}_{tf}_{sd}_{ed}.pkl"


def cache_enabled() -> bool:
    return os.getenv("DISABLE_KLINES_CACHE", "").strip().lower() not in ("1", "true", "yes", "on")


def load_cached_df(path: Path):
    if not path.is_file():
        return None
    import pandas as pd

    return pd.read_pickle(path)


def save_cached_df(path: Path, df) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_pickle(path)
