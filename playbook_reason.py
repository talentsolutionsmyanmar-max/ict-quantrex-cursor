"""
Deterministic entry/exit narratives for the trade playbook (learning / review).
No LLM — same facts the engine used, written for humans.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional, Tuple

import pandas as pd


def _b(row: pd.Series, key: str) -> bool:
    try:
        return bool(row.get(key, False))
    except Exception:
        return False


def entry_snapshot(
    row: pd.Series,
    confluence: int,
    symbol: str,
    config: Any,
    *,
    confluence_reasons: Optional[list[str]] = None,
    confluence_flags: Optional[Dict[str, bool]] = None,
    confluence_thresholds: Optional[Dict[str, float]] = None,
) -> Tuple[Dict[str, Any], str]:
    sig = int(row.get("signal", 0) or 0)
    strength = float(row.get("signal_strength", 0) or 0)
    side = "LONG" if sig == 1 else "SHORT" if sig == -1 else "FLAT"

    drivers: list[str] = []
    detail: Dict[str, Any] = {
        "symbol": symbol,
        "side": side,
        "signal": sig,
        "signal_strength": strength,
        "confluence": confluence,
        "min_confluence_required": int(getattr(config, "MIN_CONFLUENCE", 2)),
        "min_strength_required": float(getattr(config, "MIN_SIGNAL_STRENGTH", 65)),
    }
    if confluence_reasons is not None:
        detail["confluence_reasons"] = list(confluence_reasons)
    if confluence_flags is not None:
        detail["confluence_flags"] = dict(confluence_flags)
    if confluence_thresholds is not None:
        detail["confluence_thresholds"] = dict(confluence_thresholds)

    if sig == 1:
        if _b(row, "bullish_sweep"):
            detail["bullish_sweep"] = True
            drivers.append("bullish liquidity sweep + reclaim (discount context)")
        if _b(row, "bullish_fvg"):
            detail["bullish_fvg"] = True
            drivers.append("bullish FVG present")
        if _b(row, "discount"):
            detail["discount"] = True
            drivers.append("price in discount vs range")
    elif sig == -1:
        if _b(row, "bearish_sweep"):
            detail["bearish_sweep"] = True
            drivers.append("bearish liquidity sweep + rejection (premium context)")
        if _b(row, "bearish_fvg"):
            detail["bearish_fvg"] = True
            drivers.append("bearish FVG present")
        if _b(row, "premium"):
            detail["premium"] = True
            drivers.append("price in premium vs range")

    if not drivers:
        drivers.append("ICT composite signal (engine)")

    narrative = (
        f"Open {side} on {symbol}: strength {strength:.0f}% (min {detail['min_strength_required']:.0f}%), "
        f"confluence {confluence} (min {detail['min_confluence_required']}). "
        f"Drivers: {'; '.join(drivers)}."
    )
    return detail, narrative


def exit_narrative(
    exit_type: str,
    row: pd.Series,
    *,
    entry_reason_text: Optional[str],
    exit_price: float,
    bars_held: Optional[int] = None,
    reversal_min_strength: Optional[float] = None,
) -> str:
    close = float(row.get("close", exit_price) or exit_price)
    rev = (
        f"Opposite ICT signal with strength >= {float(reversal_min_strength):.0f}% — flat for conflict resolution."
        if reversal_min_strength is not None
        else "Opposite ICT signal with strength >= configured threshold — flat for conflict resolution."
    )

    base = {
        "TP1": "Partial exit: TP1 (1R) level traded through — scale-out per execution spec.",
        "TP2": "Partial exit: TP2 (2R) level traded through.",
        "TP3": "Partial / full exit: TP3 (3R) level traded through.",
        "STOP_LOSS": "Stop loss: structural + ATR stop breached on this candle (full remaining size).",
        "TRAIL_STOP": "Trailing stop: after TP1, ATR trail caught price — protecting runner.",
        "TIME_EXIT": "Time exit: held through max candle count without full target path.",
        "SIGNAL_REVERSAL": rev,
        "STOP_SESSION": "Session stop: paper loop halted with position; flattened at last price.",
    }
    chunk = base.get(exit_type, f"Exit event: {exit_type}.")
    bar = ""
    if bars_held is not None:
        bar = f" Bars in trade: {bars_held}."
    ref = ""
    if entry_reason_text:
        short = entry_reason_text if len(entry_reason_text) < 320 else entry_reason_text[:317] + "…"
        ref = f" Entry thesis (snapshot): {short}"
    return f"{chunk} Exit ~{close:.6g}.{bar}{ref}"


def entry_context_json_str(ctx: Dict[str, Any]) -> str:
    return json.dumps(ctx, default=str)
