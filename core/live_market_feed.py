from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import ccxt  # type: ignore
import pandas as pd

logger = logging.getLogger("quantrex.live_feed")


class LiveMarketFeed:
    def __init__(self, symbol: str = "BTC/USDT", timeframe: str = "15m"):
        self.exchange = ccxt.binance({"enableRateLimit": True, "options": {"defaultType": "spot"}})
        self.symbol = symbol
        self.timeframe = timeframe
        self.last_candle_ts: Optional[pd.Timestamp] = None

    def fetch_current_price(self) -> float:
        ticker = self.exchange.fetch_ticker(self.symbol)
        return float(ticker.get("last") or ticker.get("close") or 0.0)

    def fetch_live_candles(self, limit: int = 100) -> pd.DataFrame:
        ohlcv = self.exchange.fetch_ohlcv(self.symbol, self.timeframe, limit=limit)
        df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        for c in ("open", "high", "low", "close", "volume"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
        self.last_candle_ts = pd.Timestamp(df["timestamp"].iloc[-1])
        return df

    def check_new_candle(self) -> Optional[pd.Series]:
        now = datetime.now(timezone.utc)
        if self.last_candle_ts is None or (now.minute % 15 == 0 and now.second < 10):
            last = self.exchange.fetch_ohlcv(self.symbol, self.timeframe, limit=1)
            if not last:
                return None
            df_new = pd.DataFrame(last, columns=["timestamp", "open", "high", "low", "close", "volume"])
            df_new["timestamp"] = pd.to_datetime(df_new["timestamp"], unit="ms", utc=True)
            for c in ("open", "high", "low", "close", "volume"):
                df_new[c] = pd.to_numeric(df_new[c], errors="coerce")
            ts = pd.Timestamp(df_new["timestamp"].iloc[0])
            if self.last_candle_ts is None or ts != self.last_candle_ts:
                self.last_candle_ts = ts
                return df_new.iloc[0]
        return None
