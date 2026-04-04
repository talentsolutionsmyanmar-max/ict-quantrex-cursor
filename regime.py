"""
Lightweight market regime tags for PRISM-style analysis (vol + simple trend vs mean).
Uses the same OHLC dataframe as the backtester.
"""

from __future__ import annotations

from typing import Any, Dict

import pandas as pd
import numpy as np


def detect_regime(df: pd.DataFrame, lookback: int = 80) -> Dict[str, Any]:
    if df is None or len(df) < max(lookback, 25):
        return {
            "vol_regime": "unknown",
            "trend_regime": "unknown",
            "tag": "insufficient_data",
        }

    closes = df["close"].astype(float).iloc[-lookback:]
    rets = closes.pct_change().dropna()
    if len(rets) < 10:
        return {"vol_regime": "unknown", "trend_regime": "unknown", "tag": "insufficient_data"}

    rolling_vol = rets.rolling(20, min_periods=5).std()
    recent_vol = float(rolling_vol.iloc[-1])
    baseline = float(rolling_vol.median()) if rolling_vol.notna().any() else recent_vol
    if baseline <= 0 or pd.isna(baseline):
        vol_regime = "normal"
    elif recent_vol > baseline * 1.35:
        vol_regime = "high_vol"
    elif recent_vol < baseline * 0.65:
        vol_regime = "low_vol"
    else:
        vol_regime = "normal"

    sma = float(closes.mean())
    last = float(closes.iloc[-1])
    if last > sma * 1.015:
        trend = "up"
    elif last < sma * 0.985:
        trend = "down"
    else:
        trend = "range"

    tag = f"{vol_regime}_{trend}"
    return {
        "vol_regime": vol_regime,
        "trend_regime": trend,
        "tag": tag,
        "recent_vol": round(recent_vol, 6),
        "baseline_vol": round(float(baseline), 6) if not pd.isna(baseline) else None,
    }


def _wilder_ema(s: pd.Series, period: int) -> pd.Series:
    return s.ewm(alpha=1.0 / max(1, period), adjust=False).mean()


def _compute_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)

    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    tr = pd.concat(
        [
            (high - low),
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs(),
        ],
        axis=1,
    ).max(axis=1)

    atr = _wilder_ema(tr, period)
    plus_di = 100.0 * _wilder_ema(pd.Series(plus_dm, index=df.index), period) / atr.replace(0.0, np.nan)
    minus_di = 100.0 * _wilder_ema(pd.Series(minus_dm, index=df.index), period) / atr.replace(0.0, np.nan)
    dx = (100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0.0, np.nan)).fillna(0.0)
    return _wilder_ema(dx, period).fillna(0.0)


def _persistence_filter(raw_state: pd.Series, persist_bars: int) -> pd.Series:
    vals = raw_state.fillna("unknown").astype(str).tolist()
    if not vals:
        return raw_state
    pb = max(1, int(persist_bars))
    out = [vals[0]]
    candidate = vals[0]
    streak = 1
    for i in range(1, len(vals)):
        cur = vals[i]
        prev = out[-1]
        if cur == prev:
            candidate = cur
            streak = pb
            out.append(cur)
            continue
        if cur == candidate:
            streak += 1
        else:
            candidate = cur
            streak = 1
        if streak >= pb:
            out.append(cur)
        else:
            out.append(prev)
    return pd.Series(out, index=raw_state.index)


def annotate_regime(df: pd.DataFrame, config: Any) -> pd.DataFrame:
    """
    Adds regime columns used for pre-signal gating in ICT engine.
    """
    d = df.copy()
    adx_p = int(getattr(config, "REGIME_ADX_PERIOD", 14))
    atr_p = int(getattr(config, "REGIME_ATR_PERIOD", 14))
    adx_min = float(getattr(config, "REGIME_ADX_MIN", 18.0))
    atr_pct_min = float(getattr(config, "REGIME_ATR_PCT_MIN", 0.35))
    ema_fast = int(getattr(config, "REGIME_EMA_FAST", 20))
    ema_slow = int(getattr(config, "REGIME_EMA_SLOW", 50))
    persist = int(getattr(config, "REGIME_PERSIST_BARS", 3))

    # ATR %
    tr = pd.concat(
        [
            (d["high"] - d["low"]).astype(float),
            (d["high"] - d["close"].shift(1)).abs().astype(float),
            (d["low"] - d["close"].shift(1)).abs().astype(float),
        ],
        axis=1,
    ).max(axis=1)
    atr = _wilder_ema(tr, atr_p)
    atr_pct = (atr / d["close"].replace(0.0, np.nan).astype(float) * 100.0).fillna(0.0)

    # ADX + direction
    adx = _compute_adx(d, period=adx_p)
    fast = d["close"].astype(float).ewm(span=max(2, ema_fast), adjust=False).mean()
    slow = d["close"].astype(float).ewm(span=max(3, ema_slow), adjust=False).mean()
    trend_up = fast > slow
    trend_dn = fast < slow
    trend_ok = (adx >= adx_min) & (atr_pct >= atr_pct_min)

    raw = pd.Series("ranging", index=d.index, dtype="object")
    raw = raw.where(~(trend_ok & trend_up), "trend_up")
    raw = raw.where(~(trend_ok & trend_dn), "trend_down")

    filt = _persistence_filter(raw, persist)

    d["regime_adx"] = adx.astype(float)
    d["regime_atr_pct"] = atr_pct.astype(float)
    d["regime_raw"] = raw.astype(str)
    d["regime_state"] = filt.astype(str)
    return d
