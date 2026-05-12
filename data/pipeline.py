"""
Phase 0 — Crypto-native data pipeline (YAML-gated).

Fetches primary OHLCV (Binance public REST, same family as DataHandler) and optionally
attaches disabled-by-default auxiliary series. ICT detectors are unchanged; this module
is for research / alignment only until flags are enabled and validated.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
import requests

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]


def _read_spec(spec: Optional[Dict[str, Any]], spec_path: Optional[Path]) -> Dict[str, Any]:
    if spec is not None:
        return spec
    from strategy.load_spec import read_raw_spec

    return read_raw_spec(spec_path)


def _norm_binance_rest_symbol(symbol: str) -> str:
    s = symbol.upper().replace("/", "")
    if ":" in s:
        s = s.split(":")[0]
    if not s.endswith("USDT") and s.endswith("USD"):
        s = s.replace("USD", "USDT")
    if not s.endswith("USDT"):
        s = f"{s}USDT"
    return s


def _to_ccxt_symbol(symbol: str) -> str:
    """BTCUSDT -> BTC/USDT"""
    s = _norm_binance_rest_symbol(symbol)
    if s.endswith("USDT") and "/" not in s:
        return f"{s[:-4]}/USDT"
    return symbol


class CryptoNativeDataPipeline:
    """
    Load spec, fetch OHLCV, optionally merge auxiliary crypto-native sources (all off by default).

    Alignment: slower series merged with ``pd.merge_asof(..., direction='backward')`` onto
    the 15m OHLCV timeline to avoid lookahead.
    """

    def __init__(self, spec: Optional[Dict[str, Any]] = None, spec_path: Optional[Path] = None) -> None:
        self.raw = _read_spec(spec, spec_path)
        self.ds = self.raw.get("data_sources") or {}
        self.primary = self.ds.get("primary") or {}
        self.crypto = self.ds.get("crypto_native") or {}
        self.macro = self.ds.get("macro") or {}
        market = self.raw.get("market") or {}
        self._base_url = str(market.get("binance_spot_base_url") or "https://api.binance.com/api/v3").rstrip("/")
        self._grid_tf = str((self.primary.get("ohlcv") or {}).get("grid_timeframe") or market.get("timeframe") or "15m")

    def _make_ccxt_binance(self) -> Any:
        import ccxt  # type: ignore

        return ccxt.binance({"enableRateLimit": True})

    def _get_with_retries(self, url: str, params: dict, *, timeout_s: float = 30.0, retries: int = 3) -> requests.Response:
        last: Optional[Exception] = None
        for attempt in range(retries):
            try:
                r = requests.get(url, params=params, timeout=timeout_s)
                if r.status_code == 429:
                    time.sleep(min(8.0, 2**attempt))
                    continue
                return r
            except Exception as e:
                last = e
                time.sleep(min(8.0, 2**attempt))
        if last:
            raise last
        raise RuntimeError("request failed")

    def _fetch_klines(
        self,
        symbol_rest: str,
        interval: str,
        start_ms: int,
        end_ms: int,
    ) -> pd.DataFrame:
        url = f"{self._base_url}/klines"
        all_rows: list = []
        cur = start_ms
        while cur < end_ms:
            params = {
                "symbol": symbol_rest,
                "interval": interval,
                "startTime": cur,
                "endTime": end_ms,
                "limit": 1000,
            }
            resp = self._get_with_retries(url, params)
            if resp.status_code != 200:
                raise RuntimeError(f"Binance klines HTTP {resp.status_code}: {resp.text[:200]}")
            chunk = resp.json()
            if not chunk:
                break
            all_rows.extend(chunk)
            cur = int(chunk[-1][0]) + 1
        if not all_rows:
            return pd.DataFrame()
        df = pd.DataFrame(
            all_rows,
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
        for c in ("open", "high", "low", "close", "volume"):
            df[c] = df[c].astype(float)
        return df.sort_values("timestamp").reset_index(drop=True)

    def _log_data_quality_metrics(
        self,
        df: pd.DataFrame,
        source_stats: Dict[str, Any],
        *,
        symbol: str,
    ) -> None:
        """Append-only quality log for forensic review (JSON lines under logs/)."""
        obs = self.raw.get("observability") or {}
        if not obs.get("log_signals", True):
            return
        log_dir = ROOT / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc)
        ts_col = "timestamp" if "timestamp" in df.columns else None
        latest = pd.to_datetime(df[ts_col], utc=True).max() if ts_col and len(df) else now
        freshness_seconds = float((now - latest).total_seconds()) if latest == latest else float("nan")

        alignment_gap_count = 0
        if ts_col and len(df) > 1:
            tss = df[ts_col].sort_values()
            dif = tss.diff().dropna()
            # Gaps materially wider than 15m grid (allow small drift)
            alignment_gap_count = int((dif > pd.Timedelta(minutes=20)).sum())

        ohlcv_cols = {"timestamp", "open", "high", "low", "close", "volume", "close_time", "quote_volume", "trades", "taker_buy_base", "taker_buy_quote", "ignore"}
        aux_cols = [c for c in df.columns if c not in ohlcv_cols]
        if aux_cols:
            missing_value_pct = float(df[aux_cols].isna().mean().mean() * 100.0)
        else:
            missing_value_pct = 0.0

        row = {
            "kind": "data_pipeline_quality",
            "timestamp": now.isoformat(),
            "symbol": symbol,
            "freshness_seconds": freshness_seconds,
            "alignment_gap_count": alignment_gap_count,
            "missing_value_pct": round(missing_value_pct, 4),
            "grid_timeframe": self._grid_tf,
            "rows": int(len(df)),
            "sources": source_stats,
        }
        path = log_dir / "data_quality_metrics.jsonl"
        import json

        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, default=str) + "\n")

    def fetch_aligned(
        self,
        symbol: str,
        end_time: datetime,
        *,
        lookback_hours: int = 48,
    ) -> pd.DataFrame:
        """
        Return OHLCV (+ optional auxiliary columns) aligned on the primary grid.

        ``end_time`` must be timezone-aware (UTC recommended).
        """
        if end_time.tzinfo is None:
            end_time = end_time.replace(tzinfo=timezone.utc)
        end_time = end_time.astimezone(timezone.utc)
        start_time = end_time - timedelta(hours=int(lookback_hours))
        start_ms = int(start_time.timestamp() * 1000)
        end_ms = int(end_time.timestamp() * 1000)

        sym = _norm_binance_rest_symbol(symbol)
        ohlcv = self._fetch_klines(sym, self._grid_tf, start_ms, end_ms)
        if ohlcv.empty:
            logger.warning("No OHLCV returned for %s (%s)", sym, self._grid_tf)
            return ohlcv

        out = ohlcv.copy()
        source_stats: Dict[str, Any] = {"ohlcv": {"rows": len(out), "latest_ts": str(out["timestamp"].iloc[-1])}}

        # Optional: single order-book snapshot (Phase 0 prototype — not per-bar 100ms tape)
        ob_cfg = self.crypto.get("orderbook") or {}
        if ob_cfg.get("enabled"):
            try:
                ex = self._make_ccxt_binance()
                ccxt_sym = _to_ccxt_symbol(sym)
                levels = ob_cfg.get("depth_levels") or [20]
                lim = max(int(x) for x in levels) if levels else 20
                book = ex.fetch_order_book(ccxt_sym, limit=min(lim, 100))
                bids = book.get("bids") or []
                asks = book.get("asks") or []
                bid_sz = sum(float(x[1]) for x in bids[:lim]) if bids else 0.0
                ask_sz = sum(float(x[1]) for x in asks[:lim]) if asks else 0.0
                tot = bid_sz + ask_sz
                imb = (bid_sz - ask_sz) / tot if tot > 0 else 0.0
                best_bid = float(bids[0][0]) if bids else float("nan")
                best_ask = float(asks[0][0]) if asks else float("nan")
                mid = (best_bid + best_ask) / 2 if bids and asks else float("nan")
                spread_pct = ((best_ask - best_bid) / mid) if mid == mid and mid > 0 else float("nan")
                out["orderbook_imbalance"] = imb
                out["spread_pct"] = spread_pct
                source_stats["orderbook"] = {"snapshot": True, "depth_limit": lim}
            except Exception as e:
                logger.warning("orderbook fetch skipped: %s", e)
                source_stats["orderbook"] = {"error": str(e)}

        # On-chain / sentiment / macro: placeholders (implement provider clients when keys + flags on)
        for name, block, envkey in (
            ("on_chain", self.crypto.get("on_chain"), "GLASSNODE_API_KEY"),
            ("sentiment", self.crypto.get("sentiment"), "GDELT_API_KEY"),
            ("btc_dominance", self.macro.get("btc_dominance"), "COINMETRICS_API_KEY"),
        ):
            if isinstance(block, dict) and block.get("enabled"):
                key = os.environ.get(str(block.get("api_key_env") or envkey), "").strip()
                if not key and name != "sentiment":
                    logger.warning("%s enabled but missing API key (%s)", name, envkey)
                out[f"{name}_placeholder"] = pd.NA
                source_stats[name] = {"status": "not_implemented_phase0_stub"}

        sp = self.macro.get("sp500") or {}
        if sp.get("enabled"):
            try:
                import yfinance as yf  # type: ignore

                tkr = str(sp.get("ticker") or "^GSPC")
                hist = yf.Ticker(tkr).history(
                    start=start_time.date(),
                    end=(end_time + timedelta(days=1)).date(),
                    interval="1d",
                    auto_adjust=False,
                )
                if hist.empty:
                    raise RuntimeError("empty yahoo history")
                h = hist.reset_index()
                date_col = "Date" if "Date" in h.columns else h.columns[0]
                h["ts"] = pd.to_datetime(h[date_col], utc=True)
                h["sp500_close"] = h["Close"].astype(float)
                aux = h[["ts", "sp500_close"]].sort_values("ts")
                base = out.sort_values("timestamp").rename(columns={"timestamp": "ts"})
                merged = pd.merge_asof(base, aux, on="ts", direction="backward")
                out = merged.rename(columns={"ts": "timestamp"})
                source_stats["sp500"] = {"rows": len(aux)}
            except Exception as e:
                logger.warning("macro sp500 fetch skipped: %s", e)
                source_stats["sp500"] = {"error": str(e)}

        # Forward-fill sparse aux columns max 3 bars (only non-OHLCV columns)
        aux_cols = [c for c in out.columns if c not in ohlcv.columns]
        if aux_cols:
            out[aux_cols] = out[aux_cols].ffill(limit=3)

        miss = float(out[aux_cols].isna().mean().mean()) if aux_cols else 0.0
        source_stats["missing_aux_frac"] = miss
        self._log_data_quality_metrics(out, source_stats, symbol=sym)
        return out


# Example (disabled by default):
# pipe = CryptoNativeDataPipeline()
# df = pipe.fetch_aligned("BTCUSDT", datetime.now(timezone.utc), lookback_hours=24)
