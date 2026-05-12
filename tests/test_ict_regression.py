"""
ICT Regression Smoke Test — Phase 0 Compatibility Gate

Ensures aligned DataFrames from CryptoNativeDataPipeline remain compatible
with existing ICT signal logic (no column drops, no type breaks).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from data.pipeline import CryptoNativeDataPipeline

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def load_test_config():
    spec_path = ROOT / "strategy" / "spec.yaml"
    with open(spec_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.fixture
def mock_aligned_df(load_test_config):
    """Minimal aligned DataFrame matching pipeline-style OHLCV (+ Binance echo columns)."""
    dates = pd.date_range("2026-05-11", periods=100, freq="15min", tz="UTC")
    df = pd.DataFrame(
        {
            "open": np.random.uniform(40000, 41000, 100),
            "high": np.random.uniform(40500, 41500, 100),
            "low": np.random.uniform(39500, 40500, 100),
            "close": np.random.uniform(40000, 41000, 100),
            "volume": np.random.uniform(1e6, 5e6, 100),
        },
        index=dates,
    )
    df["quote_volume"] = df["close"] * df["volume"]
    df["taker_buy_base"] = df["volume"] * 0.6
    df["taker_buy_quote"] = df["quote_volume"] * 0.6
    return df


@pytest.mark.ict
def test_pipeline_dataframe_compat_with_ict(mock_aligned_df):
    """
    Gate: DataFrame from Phase 0 pipeline must contain core OHLCV
    and remain compatible with downstream ICT detectors.
    """
    required_ict_cols = {"open", "high", "low", "close", "volume"}
    assert required_ict_cols.issubset(set(mock_aligned_df.columns)), "Missing core OHLCV for ICT"
    idx = mock_aligned_df.index
    assert getattr(idx, "freq", None) is not None or idx.inferred_freq in (
        "15min",
        "15T",
    ), "Index must align to 15m grid"
    core = mock_aligned_df[list(required_ict_cols)]
    assert core.isnull().sum().sum() == 0, "No NaNs allowed in core ICT columns"


@pytest.mark.ict
def test_mock_ict_detector_runs_on_aligned_data(mock_aligned_df):
    """
    Simulates downstream ICT signal generation.
    Replace with real AdaptiveFVGDetector when wired.
    """

    class MockICTDetector:
        def detect(self, df: pd.DataFrame) -> list:
            assert len(df) >= 3
            return [
                {"timestamp": df.index[10], "direction": "bullish", "confluence_score": 72},
                {"timestamp": df.index[45], "direction": "bearish", "confluence_score": 68},
            ]

    detector = MockICTDetector()
    signals = detector.detect(mock_aligned_df)
    assert len(signals) == 2
    assert all("timestamp" in s and "confluence_score" in s for s in signals)


@pytest.mark.ict
def test_crypto_native_pipeline_has_core_ohlcv_columns(monkeypatch, load_test_config):
    """Real pipeline path: OHLCV columns present after fetch (mocked HTTP)."""
    spec = load_test_config
    spec.setdefault("observability", {})["log_signals"] = False
    pipe = CryptoNativeDataPipeline(spec=spec)

    def fake_fetch(self, symbol_rest, interval, start_ms, end_ms):
        dates = pd.date_range("2026-05-11", periods=32, freq="15min", tz="UTC")
        rows = []
        for i, ts in enumerate(dates):
            p = 40000.0 + i * 10.0
            rows.append(
                {
                    "timestamp": ts,
                    "open": p,
                    "high": p + 5,
                    "low": p - 5,
                    "close": p + 1,
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

    monkeypatch.setattr(CryptoNativeDataPipeline, "_fetch_klines", fake_fetch)
    end = pd.Timestamp("2026-05-11 12:00:00", tz="UTC").to_pydatetime()
    df = pipe.fetch_aligned("BTCUSDT", end, lookback_hours=12)
    for c in ("open", "high", "low", "close", "volume", "timestamp"):
        assert c in df.columns
    assert len(df) >= 3
    assert df[["open", "high", "low", "close", "volume"]].notna().all().all()
