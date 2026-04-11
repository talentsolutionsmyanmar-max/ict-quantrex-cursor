"""Unit tests for market_gates (mocked HTTP). Run: python scripts/test_market_gates.py"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import Config  # noqa: E402
from market_gates import evaluate_entry_gates  # noqa: E402


def test_blocks_low_liquidity() -> None:
    cfg = Config()
    gates = {"min_liquidity_usd": 9e15}
    raw = {"gates": gates, "market": {"symbol": "BTCUSDT"}}

    def fake_qv(*_a, **_k):
        return 1_000_000.0

    with patch("market_gates.fetch_quote_volume_24h", side_effect=fake_qv):
        ok, reasons = evaluate_entry_gates(symbol="ETHUSDT", gates=gates, config=cfg, raw_spec=raw)
    assert ok is False
    assert any("min_liquidity_usd" in r for r in reasons)


def test_blocks_high_funding() -> None:
    cfg = Config()
    gates = {"max_funding_rate": 0.0001}

    with patch("market_gates.fetch_quote_volume_24h", return_value=1e12), patch(
        "market_gates.fetch_abs_funding_rate", return_value=0.01
    ):
        ok, reasons = evaluate_entry_gates(symbol="SOLUSDT", gates=gates, config=cfg, raw_spec={})
    assert ok is False
    assert any("max_funding_rate" in r for r in reasons)


def test_blocks_high_correlation() -> None:
    cfg = Config()
    cfg.TIMEFRAME = "15m"
    gates = {"correlation_cap_btc": 0.1}

    with patch("market_gates.fetch_quote_volume_24h", return_value=1e12), patch(
        "market_gates.btc_return_correlation", return_value=0.99
    ):
        ok, reasons = evaluate_entry_gates(symbol="ETHUSDT", gates=gates, config=cfg, raw_spec={})
    assert ok is False
    assert any("correlation_cap_btc" in r for r in reasons)


def test_btc_skips_correlation() -> None:
    cfg = Config()
    gates = {"correlation_cap_btc": 0.5, "min_liquidity_usd": 1.0}
    with patch("market_gates.fetch_quote_volume_24h", return_value=1e12):
        ok, reasons = evaluate_entry_gates(symbol="BTCUSDT", gates=gates, config=cfg, raw_spec={})
    assert ok is True


if __name__ == "__main__":
    test_blocks_low_liquidity()
    test_blocks_high_funding()
    test_blocks_high_correlation()
    test_btc_skips_correlation()
    print("market_gates: ok")
