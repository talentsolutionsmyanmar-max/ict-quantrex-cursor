"""
Dirty execution simulator — stochastic slippage, latency, partial fills for paper convergence.
Reads dirty_execution block from strategy/spec.yaml (via raw dict).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class FillResult:
    requested_price: float
    filled_price: float
    filled_qty: float
    slippage_bps: float
    latency_ms: int
    partial_fill: bool
    sl_reason: Optional[str] = None


class DirtyFillSimulator:
    """
    Models adverse selection on fill: base bps + ATR + volume impact, uniform latency,
    Bernoulli partial fill. Disabled uses clean fill at requested price.
    """

    def __init__(self, raw_spec: Dict[str, Any], atr_series: pd.Series, volume_series: pd.Series):
        self._raw = raw_spec if isinstance(raw_spec, dict) else {}
        de = self._raw.get("dirty_execution") or {}
        if not isinstance(de, dict):
            de = {}
        self._de = de
        self.enabled = bool(de.get("enabled", False))
        sp = de.get("slippage_params") or {}
        if not isinstance(sp, dict):
            sp = {}
        self.base_bps = float(sp.get("base_bps", 15))
        self.atr_contribution = float(sp.get("atr_contribution", sp.get("atr_contrib", 0.5)))
        self.vol_exp = float(sp.get("volume_impact_exponent", 0.3))
        lat = de.get("latency_model") or {}
        if not isinstance(lat, dict):
            lat = {}
        self.latency_min = int(lat.get("min_ms", 500))
        self.latency_max = int(lat.get("max_ms", 2000))
        self.partial_fill_prob = float(de.get("partial_fill_probability", 0.15))
        self.atr = atr_series.astype(float).copy()
        self.volume = volume_series.astype(float).copy()

    def _row_atr_vol(self, idx: int) -> tuple[float, float]:
        i = max(0, min(int(idx), len(self.atr) - 1))
        atr_val = float(self.atr.iloc[i]) if len(self.atr) else 0.0
        vol_val = float(self.volume.iloc[i]) if len(self.volume) else 1.0
        vol_val = max(vol_val, 1e-12)
        return atr_val, vol_val

    def simulate_fill(
        self,
        *,
        row_index: int,
        requested_price: float,
        requested_qty: float,
        direction: str,
        symbol: str,
        rng: Optional[np.random.Generator] = None,
    ) -> FillResult:
        rng = rng or np.random.default_rng()
        price = float(requested_price)
        qty = float(requested_qty)
        if not self.enabled or price <= 0:
            return FillResult(
                requested_price=price,
                filled_price=price,
                filled_qty=qty,
                slippage_bps=0.0,
                latency_ms=0,
                partial_fill=False,
            )

        atr_val, vol_val = self._row_atr_vol(row_index)
        atr_slippage_bps = (atr_val / price) * self.atr_contribution * 1e4 if price > 0 else 0.0
        notional = abs(qty * price)
        size_impact = notional / vol_val
        vol_slippage_bps = (size_impact**self.vol_exp) * 100.0 if size_impact > 0 else 0.0
        total_slippage_bps = self.base_bps + atr_slippage_bps + vol_slippage_bps

        d = str(direction).lower()
        if d == "buy" or d == "long" or d == "1":
            filled_price = price * (1.0 + total_slippage_bps / 10000.0)
        else:
            filled_price = price * (1.0 - total_slippage_bps / 10000.0)

        lo, hi = sorted((self.latency_min, self.latency_max))
        latency_ms = int(rng.integers(lo, hi + 1))
        partial_fill = bool(rng.random() < self.partial_fill_prob)
        filled_qty = qty * float(rng.uniform(0.5, 1.0)) if partial_fill else qty

        return FillResult(
            requested_price=price,
            filled_price=float(filled_price),
            filled_qty=float(filled_qty),
            slippage_bps=float(total_slippage_bps),
            latency_ms=latency_ms,
            partial_fill=partial_fill,
            sl_reason=None,
        )

    def tag_sl_reason(
        self,
        fill: FillResult,
        candle: pd.Series,
        stop_price: float,
        confluence_score: float,
        atr_at_entry: float,
    ) -> str:
        """Post-trade stop diagnosis (best-effort heuristics)."""
        high = float(candle.get("high", 0) or 0)
        low = float(candle.get("low", 0) or 0)
        close = float(candle.get("close", 0) or 0)
        open_ = float(candle.get("open", close) or close)
        stop = float(stop_price)
        span = max(high - low, 1e-12)

        if fill.latency_ms >= 1500 and abs(fill.filled_price - fill.requested_price) / max(fill.requested_price, 1e-12) > 0.002:
            return "latency"

        if confluence_score < 70.0:
            return "low_confluence"

        if atr_at_entry > 0 and span > 3.5 * atr_at_entry:
            return "wick"

        if min(abs(close - stop), abs(open_ - stop)) < 0.15 * span:
            return "close_near_stop"

        return "market_move"


def neutral_fill(requested_price: float, requested_qty: float = 1.0) -> FillResult:
    """Synthetic fill when no dirty fill was recorded (e.g. clean backtest path)."""
    p = float(requested_price)
    q = float(requested_qty)
    return FillResult(
        requested_price=p,
        filled_price=p,
        filled_qty=q,
        slippage_bps=0.0,
        latency_ms=0,
        partial_fill=False,
        sl_reason=None,
    )
