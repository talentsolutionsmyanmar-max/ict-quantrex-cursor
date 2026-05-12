"""Unit tests for Phase 0 CryptoNativeDataPipeline."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from data.pipeline import CryptoNativeDataPipeline, _norm_binance_rest_symbol


def _minimal_spec() -> dict:
    return {
        "market": {
            "symbol": "BTCUSDT",
            "timeframe": "15m",
            "binance_spot_base_url": "https://api.binance.com/api/v3",
        },
        "observability": {"log_signals": False},
        "data_sources": {
            "primary": {"ohlcv": {"exchange": "binance", "grid_timeframe": "15m"}},
            "crypto_native": {
                "on_chain": {"enabled": False},
                "orderbook": {"enabled": False},
                "sentiment": {"enabled": False},
            },
            "macro": {"btc_dominance": {"enabled": False}, "sp500": {"enabled": False}},
        },
    }


def _synthetic_ohlcv(n: int = 20, start: datetime | None = None) -> pd.DataFrame:
    start = start or datetime(2024, 1, 1, tzinfo=timezone.utc)
    rows = []
    for i in range(n):
        ts = start + timedelta(minutes=15 * i)
        price = 40000.0 + i * 2.0
        rows.append(
            {
                "timestamp": ts,
                "open": price,
                "high": price + 5,
                "low": price - 5,
                "close": price + 1,
                "volume": 100.0,
                "close_time": ts,
                "quote_volume": 1.0,
                "trades": 10,
                "taker_buy_base": 1.0,
                "taker_buy_quote": 1.0,
                "ignore": 0,
            }
        )
    return pd.DataFrame(rows)


@pytest.mark.data_pipeline
def test_norm_symbol():
    assert _norm_binance_rest_symbol("BTC/USDT") == "BTCUSDT"
    assert _norm_binance_rest_symbol("BTCUSDT") == "BTCUSDT"


@pytest.mark.data_pipeline
def test_fetch_aligned_minimal(monkeypatch):
    spec = _minimal_spec()
    pipe = CryptoNativeDataPipeline(spec=spec)

    def fake_fetch(self, symbol_rest, interval, start_ms, end_ms):
        return _synthetic_ohlcv(30)

    monkeypatch.setattr(CryptoNativeDataPipeline, "_fetch_klines", fake_fetch)
    end = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    df = pipe.fetch_aligned("BTCUSDT", end, lookback_hours=24)
    assert not df.empty
    for col in ("open", "high", "low", "close", "volume"):
        assert col in df.columns
        assert df[col].notna().all()
    assert "orderbook_imbalance" not in df.columns


@pytest.mark.data_pipeline
def test_on_chain_enabled_stub(monkeypatch):
    spec = _minimal_spec()
    spec["data_sources"]["crypto_native"]["on_chain"] = {
        "enabled": True,
        "provider": "glassnode",
        "api_key_env": "GLASSNODE_API_KEY",
        "metrics": ["nvt_ratio"],
        "frequency": "1h",
    }
    pipe = CryptoNativeDataPipeline(spec=spec)

    def fake_fetch(self, symbol_rest, interval, start_ms, end_ms):
        return _synthetic_ohlcv(25)

    monkeypatch.setattr(CryptoNativeDataPipeline, "_fetch_klines", fake_fetch)
    end = datetime(2024, 1, 1, 8, 0, tzinfo=timezone.utc)
    df = pipe.fetch_aligned("BTCUSDT", end, lookback_hours=12)
    assert "on_chain_placeholder" in df.columns


@pytest.mark.data_pipeline
def test_orderbook_imbalance_range(monkeypatch):
    spec = _minimal_spec()
    spec["data_sources"]["crypto_native"]["orderbook"] = {"enabled": True, "depth_levels": [5, 10]}
    pipe = CryptoNativeDataPipeline(spec=spec)

    def fake_fetch(self, symbol_rest, interval, start_ms, end_ms):
        return _synthetic_ohlcv(15)

    class Ex:
        def fetch_order_book(self, symbol, limit=20):
            return {"bids": [[40000.0, 3.0]], "asks": [[40001.0, 1.0]]}

    monkeypatch.setattr(CryptoNativeDataPipeline, "_fetch_klines", fake_fetch)
    monkeypatch.setattr(CryptoNativeDataPipeline, "_make_ccxt_binance", lambda self: Ex())

    end = datetime(2024, 1, 1, 4, 0, tzinfo=timezone.utc)
    df = pipe.fetch_aligned("BTCUSDT", end, lookback_hours=6)
    assert "orderbook_imbalance" in df.columns
    imb = float(df["orderbook_imbalance"].iloc[0])
    assert -1.0 <= imb <= 1.0


@pytest.mark.data_pipeline
def test_merge_asof_backward_uses_prior_sp500_only(monkeypatch):
    """S&P row at T should not appear on OHLCV bars strictly before T (backward asof)."""
    import types

    spec = _minimal_spec()
    spec["data_sources"]["macro"]["sp500"] = {"enabled": True, "provider": "yahoo_finance", "ticker": "^GSPC"}
    pipe = CryptoNativeDataPipeline(spec=spec)

    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    ohlcv = _synthetic_ohlcv(48, start=start)

    def fake_fetch(self, symbol_rest, interval, start_ms, end_ms):
        return ohlcv

    class Ticker:
        def history(self, **kwargs):
            return pd.DataFrame(
                {
                    "Date": [pd.Timestamp("2024-01-01 06:00:00+0000")],
                    "Close": [123.45],
                }
            )

    yfinance_mod = types.ModuleType("yfinance")
    yfinance_mod.Ticker = lambda t: Ticker()

    monkeypatch.setattr(CryptoNativeDataPipeline, "_fetch_klines", fake_fetch)

    import builtins

    real_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "yfinance":
            return yfinance_mod
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    end = datetime(2024, 1, 1, 10, 0, tzinfo=timezone.utc)
    df = pipe.fetch_aligned("BTCUSDT", end, lookback_hours=24)
    before = df.loc[df["timestamp"] < pd.Timestamp("2024-01-01 06:00:00+00:00"), "sp500_close"]
    assert before.isna().all()
    after = df.loc[df["timestamp"] >= pd.Timestamp("2024-01-01 06:00:00+00:00"), "sp500_close"]
    assert after.notna().any()
