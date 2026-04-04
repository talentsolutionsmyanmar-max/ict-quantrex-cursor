"""
Shared ICT trade management: structural + ATR stops, 1:1 / 2:1 / 3:1 scale-out,
trailing stop after TP1, and risk-based position sizing (aligned with backtester).
"""

from __future__ import annotations

from typing import Any, Dict, List, TypedDict

import pandas as pd

from config import Config


class ConfluenceContribution(TypedDict):
    reason: str
    points: float
    active: bool


class ConfluenceBreakdown(TypedDict):
    count: int
    reasons: List[str]
    contributions: List[ConfluenceContribution]
    total_strength_score: float
    flags: Dict[str, bool]
    thresholds: Dict[str, float]


def atr_at_index(df: pd.DataFrame, idx: int, period: int = 14) -> float:
    if idx < 1:
        return float(df["high"].iloc[idx] - df["low"].iloc[idx])

    tr_values = []
    for i in range(max(0, idx - period), idx):
        high = float(df["high"].iloc[i])
        low = float(df["low"].iloc[i])
        prev_close = float(df["close"].iloc[i - 1]) if i > 0 else high
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        tr_values.append(tr)

    return float(sum(tr_values) / len(tr_values)) if tr_values else 0.0


def confluence_breakdown(row: pd.Series, config: Config) -> ConfluenceBreakdown:
    """
    Explainable confluence scoring (audit-ready).

    Important: This must be driven by config (no hardcoded thresholds), otherwise
    evolution + runtime gene changes won't match what the engine logs.
    """
    sig = int(row.get("signal", 0) or 0)
    strength = float(row.get("signal_strength", 0) or 0.0)
    min_strength = float(getattr(config, "MIN_SIGNAL_STRENGTH", 70))

    flags: Dict[str, bool] = {
        "fvg": bool(row.get("bullish_fvg", False) or row.get("bearish_fvg", False)),
        "sweep": bool(row.get("bullish_sweep", False) or row.get("bearish_sweep", False)),
        "pd_context": bool(
            (sig == 1 and row.get("discount", False)) or (sig == -1 and row.get("premium", False))
        ),
        "strength_gate": bool(strength >= min_strength),
    }

    reasons: List[str] = []
    if flags["sweep"]:
        reasons.append("liquidity_sweep")
    if flags["fvg"]:
        reasons.append("fvg")
    if flags["pd_context"]:
        reasons.append("premium_discount_context")
    if flags["strength_gate"]:
        reasons.append("strength_gate")

    # Strength contributions (model definition, not genes).
    # These mirror the scoring used to build row.signal_strength.
    # total_strength_score is the already-computed composite (0–100).
    contrib: List[ConfluenceContribution] = []
    sweep_on = bool(flags["sweep"])
    fvg_on = bool(flags["fvg"])
    pd_on = bool(flags["pd_context"])
    # OTE is not part of confluence count, but it contributes to strength.
    ote_on = bool(row.get("ote_hit", False))

    contrib.append({"reason": "liquidity_sweep", "points": 30.0, "active": sweep_on})
    contrib.append({"reason": "fvg", "points": 25.0, "active": fvg_on})
    contrib.append({"reason": "premium_discount_context", "points": 25.0, "active": pd_on})
    contrib.append({"reason": "ote", "points": 20.0, "active": ote_on})

    count = int(sum(1 for v in flags.values() if v))
    return {
        "count": count,
        "reasons": reasons,
        "contributions": contrib,
        "total_strength_score": float(max(0.0, min(100.0, strength))),
        "flags": flags,
        "thresholds": {"min_signal_strength": min_strength},
    }


def confluence_count(row: pd.Series, config: Config) -> int:
    """Confluence score for entry gating (always config-driven)."""
    return int(confluence_breakdown(row, config).get("count", 0))


def format_confluence_pretty(cx: ConfluenceBreakdown, *, min_confluence_required: int) -> str:
    """
    Human-readable summary for logs and playbook.
    Example: "Confluence: 3/2 | Strength: 82 | Reasons: fvg+liquidity_sweep+premium_discount_context"
    """
    c = int(cx.get("count", 0))
    strength = float(cx.get("total_strength_score", 0.0))
    reasons = cx.get("reasons") or []
    rs = "+".join(reasons) if reasons else "none"

    # Compact contribution points (active only), e.g. "fvg(25)+liq_sweep(30)+pd(25)".
    short = {
        "liquidity_sweep": "liq_sweep",
        "premium_discount_context": "pd",
        "strength_gate": "strength_gate",
        "fvg": "fvg",
        "ote": "ote",
    }
    contrib = cx.get("contributions") or []
    parts: List[str] = []
    for x in contrib:
        try:
            if not bool(x.get("active", False)):
                continue
            r = str(x.get("reason") or "")
            pts = float(x.get("points", 0.0))
            parts.append(f"{short.get(r, r)}({pts:.0f})")
        except Exception:
            continue
    contrib_s = "+".join(parts) if parts else "none"
    return (
        f"Confluence: {c}/{int(min_confluence_required)} | Strength: {strength:.0f} | "
        f"Reasons: {rs} | Points: {contrib_s}"
    )


def compute_sl_tp(position: int, entry: float, row: pd.Series, atr: float, config: Config) -> Dict[str, Any]:
    """
    ICT-aware levels:
    - Long: stop = min(ATR stop, liquidity swing low minus buffer) when swing exists.
    - Short: stop = max(ATR stop, liquidity swing high plus buffer).
    - TPs at 1R, 2R, 3R off entry using final risk distance.
    """
    mult = float(config.ATR_MULTIPLIER)
    buf = float(config.LIQUIDITY_BUFFER)
    entry = float(entry)
    atr = float(atr)

    if position == 1:
        atr_sl = entry - atr * mult
        struct = row.get("liquidity_low_prev")
        if pd.notna(struct):
            struct_sl = float(struct) - entry * buf
            stop = min(atr_sl, struct_sl) if struct_sl < entry else atr_sl
        else:
            stop = atr_sl
        if stop >= entry:
            stop = atr_sl
        risk_d = entry - stop
    else:
        atr_sl = entry + atr * mult
        struct = row.get("liquidity_high_prev")
        if pd.notna(struct):
            struct_sl = float(struct) + entry * buf
            stop = max(atr_sl, struct_sl) if struct_sl > entry else atr_sl
        else:
            stop = atr_sl
        if stop <= entry:
            stop = atr_sl
        risk_d = stop - entry

    if risk_d <= 0:
        risk_d = max(entry * 0.005, 1e-8)

    if position == 1:
        tp1 = entry + risk_d * float(config.TP1_RATIO)
        tp2 = entry + risk_d * float(config.TP2_RATIO)
        tp3 = entry + risk_d * float(config.TP3_RATIO)
    else:
        tp1 = entry - risk_d * float(config.TP1_RATIO)
        tp2 = entry - risk_d * float(config.TP2_RATIO)
        tp3 = entry - risk_d * float(config.TP3_RATIO)

    return {
        "stop_loss": float(stop),
        "tp1": float(tp1),
        "tp2": float(tp2),
        "tp3": float(tp3),
        "risk_distance": float(risk_d),
    }


def check_tp_hit(position: int, row: pd.Series, tp_level: float) -> bool:
    if position == 1:
        return float(row["high"]) >= float(tp_level)
    return float(row["low"]) <= float(tp_level)


def check_sl_hit(position: int, row: pd.Series, sl_level: float) -> bool:
    if position == 1:
        return float(row["low"]) <= float(sl_level)
    return float(row["high"]) >= float(sl_level)


def trail_stop_price(position: int, entry: float, atr_at_entry: float, row: pd.Series, config: Config) -> float:
    trail_distance = float(atr_at_entry) * float(config.TRAIL_ATR_MULTIPLIER)
    if position == 1:
        return max(float(entry), float(row["close"]) - trail_distance)
    return min(float(entry), float(row["close"]) + trail_distance)


def position_size_risk(capital: float, entry: float, stop_price: float, config: Config) -> float:
    risk_amount = float(capital) * float(config.RISK_PER_TRADE)
    denom = abs(float(entry) - float(stop_price))
    if denom <= 0:
        denom = float(entry) * 0.01
    return risk_amount / denom


def close_partial_pnl(
    position: int,
    exit_price: float,
    entry: float,
    fraction: float,
    capital: float,
    stop_price: float,
    config: Config,
) -> float:
    exit_price = float(exit_price)
    entry = float(entry)
    pnl_pct = (exit_price - entry) / entry if position == 1 else (entry - exit_price) / entry
    sz = position_size_risk(capital, entry, stop_price, config)
    pnl = pnl_pct * sz * float(fraction) * entry
    commission = abs(pnl) * float(config.COMMISSION)
    slippage = abs(pnl) * float(config.SLIPPAGE)
    return float(pnl - commission - slippage)


def unrealized_pnl(
    position: int,
    entry: float,
    current: float,
    remaining_fraction: float,
    capital: float,
    stop_price: float,
    config: Config,
) -> float:
    current = float(current)
    entry = float(entry)
    pnl_pct = (current - entry) / entry if position == 1 else (entry - current) / entry
    sz = position_size_risk(capital, entry, stop_price, config)
    return float(pnl_pct * sz * float(remaining_fraction) * entry)
