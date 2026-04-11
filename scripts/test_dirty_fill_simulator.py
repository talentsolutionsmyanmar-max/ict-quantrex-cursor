"""Smoke tests for dirty fill simulator (run: python scripts/test_dirty_fill_simulator.py)."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from execution.dirty_fill_simulator import DirtyFillSimulator, neutral_fill  # noqa: E402


def test_disabled_is_clean() -> None:
    raw = {"dirty_execution": {"enabled": False}}
    n = 50
    atr = pd.Series([100.0] * n)
    vol = pd.Series([1e9] * n)
    sim = DirtyFillSimulator(raw, atr, vol)
    f = sim.simulate_fill(row_index=10, requested_price=50000.0, requested_qty=1.0, direction="buy", symbol="BTCUSDT")
    assert f.filled_price == 50000.0
    assert f.slippage_bps == 0.0


def test_buy_worse_than_mid() -> None:
    raw = {"dirty_execution": {"enabled": True, "slippage_params": {"base_bps": 100}, "partial_fill_probability": 0.0}}
    n = 20
    atr = pd.Series([200.0] * n)
    vol = pd.Series([1e9] * n)
    sim = DirtyFillSimulator(raw, atr, vol)
    f = sim.simulate_fill(
        row_index=5,
        requested_price=100.0,
        requested_qty=1.0,
        direction="buy",
        symbol="BTCUSDT",
    )
    assert f.filled_price > 100.0


def test_tag_sl_reason() -> None:
    raw = {"dirty_execution": {"enabled": True}}
    atr = pd.Series([1.0, 1.0, 1.0])
    vol = pd.Series([1e6, 1e6, 1e6])
    sim = DirtyFillSimulator(raw, atr, vol)
    fill = neutral_fill(100.0, 1.0)
    row = pd.Series({"high": 110.0, "low": 85.0, "close": 90.0, "open": 100.0})
    tag = sim.tag_sl_reason(fill, row, stop_price=88.0, confluence_score=50.0, atr_at_entry=2.0)
    assert tag in ("wick", "low_confluence", "close_near_stop", "latency", "market_move")


if __name__ == "__main__":
    test_disabled_is_clean()
    test_buy_worse_than_mid()
    test_tag_sl_reason()
    print("dirty_fill_simulator: ok")
