"""
Shared ICT trade management: structural + ATR stops, 1:1 / 2:1 / 3:1 scale-out,
trailing stop after TP1, and risk-based position sizing (aligned with backtester).
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple, TypedDict
import math

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


def compute_sl_tp(
    position: int,
    entry: float,
    row: pd.Series,
    atr: float,
    config: Config,
    *,
    atr_multiplier_override: float | None = None,
) -> Dict[str, Any]:
    """
    ICT-aware levels:
    - Long: stop = min(ATR stop, liquidity swing low minus buffer) when swing exists.
    - Short: stop = max(ATR stop, liquidity swing high plus buffer).
    - TPs at 1R, 2R, 3R off entry using final risk distance.
    """
    mult = float(atr_multiplier_override) if atr_multiplier_override is not None else float(config.ATR_MULTIPLIER)
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


def regime_risk_overrides(row: pd.Series, config: Config, raw_spec: Dict[str, Any]) -> Tuple[float, float]:
    """
    Resolve regime-driven risk overrides from spec.
    Returns: (atr_multiplier_for_stop, size_multiplier_for_risk_amount)
    """
    base_atr_mult = float(getattr(config, "ATR_MULTIPLIER", 1.8))
    size_mult = 1.0

    reg = raw_spec.get("regime") if isinstance(raw_spec, dict) else {}
    actions = (reg or {}).get("regime_actions") if isinstance(reg, dict) else {}
    if not isinstance(actions, dict):
        return base_atr_mult, size_mult

    state = str(row.get("regime_state") or "").strip().lower()
    atr_pct = float(row.get("regime_atr_pct", 0.0) or 0.0)
    atr_min = float(getattr(config, "REGIME_ATR_PCT_MIN", 0.35) or 0.35)

    # Map engine states to spec buckets.
    candidates: List[Dict[str, Any]] = []
    if state == "ranging" and isinstance(actions.get("ranging"), dict):
        candidates.append(actions["ranging"])
    if state in {"trend_up", "trend_down"} and isinstance(actions.get("trending"), dict):
        candidates.append(actions["trending"])
    # Directional trend buckets: size/quality tuned separately from generic "trending".
    if state == "trend_down" and isinstance(actions.get("trend_down"), dict):
        candidates.append(actions["trend_down"])
    if state == "trend_up" and isinstance(actions.get("trend_up"), dict):
        candidates.append(actions["trend_up"])
    # Optional high-vol overlay; no dedicated high_vol state in annotate_regime.
    if atr_pct >= (atr_min * 1.75) and isinstance(actions.get("high_vol"), dict):
        candidates.append(actions["high_vol"])

    atr_mult = base_atr_mult
    for a in candidates:
        rps = a.get("reduce_position_size")
        if rps is not None:
            try:
                size_mult *= max(0.05, min(1.0, float(rps)))
            except Exception:
                pass
        w = a.get("widen_stop_atr_multiplier")
        if w is not None:
            try:
                atr_mult = max(atr_mult, float(w))
            except Exception:
                pass
    return atr_mult, size_mult


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


def _bars_per_year_from_timeframe(tf: str) -> float:
    s = str(tf or "").strip().lower()
    if not s:
        return 365.0 * 24.0 * 4.0  # fallback: 15m
    unit = s[-1]
    try:
        n = int(s[:-1])
    except Exception:
        n = 15
        unit = "m"
    mins = {
        "m": float(n),
        "h": float(n) * 60.0,
        "d": float(n) * 1440.0,
        "w": float(n) * 10080.0,
    }.get(unit, 15.0)
    return (365.0 * 24.0 * 60.0) / max(mins, 1.0)


def position_size_risk(
    capital: float,
    entry: float,
    stop_price: float,
    config: Config,
    *,
    atr: float = 0.0,
    size_multiplier: float = 1.0,
) -> float:
    risk_amount = float(capital) * float(config.RISK_PER_TRADE)
    denom = abs(float(entry) - float(stop_price))
    if denom <= 0:
        denom = float(entry) * 0.01
    method = str(getattr(config, "SIZING_METHOD", "fixed_risk") or "fixed_risk").lower()
    if method == "volatility_targeting":
        atr_pct = (float(atr) / max(float(entry), 1e-12)) if float(atr) > 0 else 0.0
        if atr_pct > 0:
            bars_per_year = _bars_per_year_from_timeframe(getattr(config, "TIMEFRAME", "15m"))
            est_annual_vol = atr_pct * math.sqrt(max(1.0, bars_per_year))
            target = float(getattr(config, "VOLATILITY_TARGET_ANNUAL", 0.15) or 0.15)
            scale = target / max(est_annual_vol, 1e-9)
            # Keep leverage response bounded so sizing remains stable in production.
            risk_amount *= max(0.25, min(1.5, scale))
    risk_amount *= max(0.05, float(size_multiplier))
    return risk_amount / denom


def close_partial_pnl(
    position: int,
    exit_price: float,
    entry: float,
    fraction: float,
    entry_qty: float,
    config: Config,
) -> float:
    exit_price = float(exit_price)
    entry = float(entry)
    pnl_pct = (exit_price - entry) / entry if position == 1 else (entry - exit_price) / entry
    qty = max(0.0, float(entry_qty) * float(fraction))
    pnl = pnl_pct * qty * entry
    turnover = qty * (abs(entry) + abs(exit_price))
    commission = turnover * float(config.COMMISSION)
    slippage = turnover * float(config.SLIPPAGE)
    return float(pnl - commission - slippage)


def unrealized_pnl(
    position: int,
    entry: float,
    current: float,
    remaining_fraction: float,
    entry_qty: float,
    config: Config,
) -> float:
    current = float(current)
    entry = float(entry)
    pnl_pct = (current - entry) / entry if position == 1 else (entry - current) / entry
    qty = max(0.0, float(entry_qty) * float(remaining_fraction))
    return float(pnl_pct * qty * entry)
