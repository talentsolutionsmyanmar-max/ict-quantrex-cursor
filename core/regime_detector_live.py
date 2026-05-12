from __future__ import annotations

import pandas as pd


def detect_regime_live(df: pd.DataFrame, lookback: int = 40) -> str:
    """Streaming-safe regime detection using EMA slope + volatility proxy."""
    if df is None or len(df) < int(lookback):
        return "initializing"

    closes = pd.to_numeric(df["close"], errors="coerce").tail(int(lookback))
    ema20 = closes.ewm(span=20, adjust=False).mean()
    ema50 = closes.ewm(span=50, adjust=False).mean()

    if len(ema20) < 11:
        return "initializing"

    base = float(ema20.iloc[-10]) if float(ema20.iloc[-10]) != 0 else 1e-9
    slope_ema = (float(ema20.iloc[-1]) - float(ema20.iloc[-10])) / base
    ema_aligned = float(ema20.iloc[-1]) > float(ema50.iloc[-1]) if slope_ema > 0 else float(ema20.iloc[-1]) < float(
        ema50.iloc[-1]
    )

    h = pd.to_numeric(df["high"], errors="coerce").tail(20)
    l = pd.to_numeric(df["low"], errors="coerce").tail(20)
    c = pd.to_numeric(df["close"], errors="coerce").tail(20)
    _atr_proxy = float((h - l).mean())
    avg_range = float(c.pct_change().abs().mean())
    is_high_vol = avg_range > 0.015

    if abs(slope_ema) < 0.001 and not is_high_vol:
        return "normal_range"
    if is_high_vol and abs(slope_ema) < 0.003:
        return "chop"
    if slope_ema > 0 and ema_aligned:
        return "trend_up"
    if slope_ema < 0 and ema_aligned:
        return "trend_down"
    return "normal_range"
