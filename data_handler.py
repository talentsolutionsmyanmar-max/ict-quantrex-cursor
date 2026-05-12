from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import requests
from datetime import datetime, timedelta, timezone
import time
from config import Config
from klines_cache import cache_enabled, cache_path, load_cached_df, save_cached_df


class DataHandler:
    def __init__(self, config: Config):
        self.config = config
        self.base_url = config.BINANCE_API
        self._session = requests.Session()

    def _get_with_retries(self, url: str, params: dict, timeout_s: int = 60, retries: int = 5) -> requests.Response:
        last_err = None
        for attempt in range(retries):
            try:
                resp = self._session.get(url, params=params, timeout=timeout_s)
                return resp
            except Exception as e:
                last_err = e
                # Exponential backoff: 1s, 2s, 4s, 8s...
                time.sleep(min(16, 2**attempt))
        raise last_err

    def fetch_historical_data(self, start_date: str, end_date: str) -> pd.DataFrame:
        """Fetch historical klines from Binance (uses disk cache when enabled)."""
        parq = os.environ.get("BINANCE_KLINES_PARQUET", "").strip()
        if parq:
            p = Path(parq)
            if p.is_file():
                if p.suffix.lower() in (".parquet", ".pq"):
                    df = pd.read_parquet(p)
                else:
                    df = pd.read_pickle(p)
                if "timestamp" not in df.columns and df.index.name == "timestamp":
                    df = df.reset_index()
                if "timestamp" in df.columns:
                    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
                if df.index.name is None and "timestamp" in df.columns:
                    df = df.set_index("timestamp", drop=False)
                return df

        if cache_enabled():
            cpath = cache_path(
                symbol=self.config.SYMBOL,
                interval=self.config.TIMEFRAME,
                start_date=start_date,
                end_date=end_date,
            )
            cached = load_cached_df(cpath)
            if cached is not None and not cached.empty:
                return cached

        # Interpret user-provided dates as UTC calendar days (not local time),
        # so 2024-01-01 always maps to 2024-01-01T00:00:00Z.
        start_dt = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        # Make end_date inclusive by using next day midnight (UTC) as the exclusive upper bound.
        end_dt_exclusive = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(days=1)

        start_ts = int(start_dt.timestamp() * 1000)
        end_ts = int(end_dt_exclusive.timestamp() * 1000)

        all_klines = []
        current_ts = start_ts

        while current_ts < end_ts:
            url = f"{self.base_url}/klines"
            params = {
                "symbol": self.config.SYMBOL,
                "interval": self.config.TIMEFRAME,
                "startTime": current_ts,
                "endTime": end_ts,
                "limit": 1000,
            }

            resp = self._get_with_retries(url, params=params, timeout_s=60, retries=5)
            if resp.status_code != 200:
                raise Exception(f"API Error: {resp.text}")

            klines = resp.json()
            if not klines:
                break

            all_klines.extend(klines)
            current_ts = klines[-1][0] + 1

        df = pd.DataFrame(
            all_klines,
            columns=[
                "timestamp",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "close_time",
                "quote_volume",
                "trades",
                "taker_buy_base",
                "taker_buy_quote",
                "ignore",
            ],
        )

        if df.empty:
            raise Exception("No klines returned for requested date range.")

        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        numeric_cols = ["open", "high", "low", "close", "volume"]
        df[numeric_cols] = df[numeric_cols].astype(float)

        # Keep timestamp both as index (for timeseries ops) and as a column (for JSON/UI)
        df = df.set_index("timestamp", drop=False)

        if cache_enabled():
            cpath = cache_path(
                symbol=self.config.SYMBOL,
                interval=self.config.TIMEFRAME,
                start_date=start_date,
                end_date=end_date,
            )
            try:
                save_cached_df(cpath, df)
            except Exception:
                pass

        return df

    def fetch_live_data(self, limit: int = 100, symbol: str | None = None) -> pd.DataFrame:
        """Fetch recent live klines (symbol defaults to config.SYMBOL)."""
        sym = (symbol or self.config.SYMBOL).upper().replace("/", "")
        url = f"{self.base_url}/klines"
        params = {
            "symbol": sym,
            "interval": self.config.TIMEFRAME,
            "limit": limit,
        }

        resp = self._get_with_retries(url, params=params, timeout_s=30, retries=3)
        if resp.status_code != 200:
            raise Exception(f"API Error: {resp.text}")

        klines = resp.json()
        df = pd.DataFrame(
            klines,
            columns=[
                "timestamp",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "close_time",
                "quote_volume",
                "trades",
                "taker_buy_base",
                "taker_buy_quote",
                "ignore",
            ],
        )

        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        numeric_cols = ["open", "high", "low", "close", "volume"]
        df[numeric_cols] = df[numeric_cols].astype(float)

        df = df.set_index("timestamp", drop=False)
        return df

