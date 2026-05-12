"""
Gbrain-style promotion controller for Karpathy-loop autoresearch.

Takes evolution output and applies hard objective gates before GO/HOLD.
This keeps autoresearch from overfitting by requiring minimum trade depth
and floor metrics on the held-out promotion aggregate.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List


def _f(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except Exception:
        return float(default)


def _i(x: Any, default: int = 0) -> int:
    try:
        return int(x)
    except Exception:
        return int(default)


def evaluate_promotion_candidate(result: Dict[str, Any]) -> Dict[str, Any]:
    """
    Decide GO/HOLD from evolution output.

    Gates (env-overridable):
    - GBRAIN_MIN_TRADES_ALL (default 80)
    - GBRAIN_MIN_SHARPE (default 0.10)
    - GBRAIN_MIN_PROFIT_FACTOR (default 1.00)
    - GBRAIN_MAX_WORST_DD_PCT (default 20.0)
    """
    top = (result or {}).get("top") or []
    if not top or not isinstance(top, list) or not isinstance(top[0], dict):
        return {
            "decision": "HOLD",
            "cqe_ack": False,
            "note": "No top candidate found from evolution.",
            "aggregate": {},
            "breaches": ["missing_top_candidate"],
        }

    rank1 = top[0]
    agg = rank1.get("promotion_aggregate") if isinstance(rank1.get("promotion_aggregate"), dict) else {}
    trades = _i(agg.get("total_trades_all"), 0)
    min_sharpe = _f(agg.get("min_sharpe"), 0.0)
    min_pf = _f(agg.get("min_profit_factor"), 0.0)
    worst_dd = abs(_f(agg.get("worst_max_drawdown_pct"), 0.0))

    floors = {
        "min_trades_all": _i(os.getenv("GBRAIN_MIN_TRADES_ALL", "80"), 80),
        "min_sharpe": _f(os.getenv("GBRAIN_MIN_SHARPE", "0.10"), 0.10),
        "min_profit_factor": _f(os.getenv("GBRAIN_MIN_PROFIT_FACTOR", "1.00"), 1.00),
        "max_worst_dd_pct": _f(os.getenv("GBRAIN_MAX_WORST_DD_PCT", "20.0"), 20.0),
    }

    breaches: List[str] = []
    if trades < floors["min_trades_all"]:
        breaches.append("min_trades_all")
    if min_sharpe < floors["min_sharpe"]:
        breaches.append("min_sharpe")
    if min_pf < floors["min_profit_factor"]:
        breaches.append("min_profit_factor")
    if worst_dd > floors["max_worst_dd_pct"]:
        breaches.append("max_worst_dd_pct")

    decision = "GO" if not breaches else "HOLD"
    note = (
        f"Gbrain {'GO' if decision == 'GO' else 'HOLD'} | "
        f"trades={trades} sharpe={min_sharpe:.3f} pf={min_pf:.3f} worst_dd={worst_dd:.2f}% | "
        f"floors={floors} breaches={breaches or 'none'}"
    )

    return {
        "decision": decision,
        "cqe_ack": decision == "GO",
        "note": note,
        "aggregate": {
            **agg,
            "gbrain_floors": floors,
            "gbrain_breaches": breaches,
        },
        "breaches": breaches,
        "rank1_genes": rank1.get("genes") if isinstance(rank1.get("genes"), dict) else {},
        "verify_window": (result or {}).get("test_window") if isinstance((result or {}).get("test_window"), list) else [],
        "symbols": (result or {}).get("symbols") if isinstance((result or {}).get("symbols"), list) else [],
    }

