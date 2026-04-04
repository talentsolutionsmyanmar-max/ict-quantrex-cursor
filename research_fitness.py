"""
Composite fitness for strategy search: OOS-weighted Sharpe, drawdown penalty, crisis floor, parsimony.
Implements the AutoQuant-style idea (optimize for robustness, not in-sample Sharpe alone) without the swarm.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def _safe_sharpe(m: Dict[str, Any]) -> float:
    if m.get("error"):
        return -3.0
    try:
        return float(m.get("sharpe_ratio", 0.0))
    except (TypeError, ValueError):
        return -3.0


def _safe_dd(m: Dict[str, Any]) -> float:
    """Max drawdown as fraction (positive number, e.g. 0.12 for 12%)."""
    if m.get("error"):
        return 1.0
    try:
        return max(0.0, float(m.get("max_drawdown", 100.0)) / 100.0)
    except (TypeError, ValueError):
        return 1.0


def composite_fitness(
    train_metrics: Dict[str, Any],
    test_metrics: Dict[str, Any],
    crisis_metrics_list: Optional[List[Dict[str, Any]]] = None,
    *,
    parsimony_penalty: float = 0.0,
    w_oos_sharpe: float = 0.55,
    w_is_sharpe: float = 0.25,
    w_dd: float = 0.35,
    w_crisis_min: float = 0.15,
    crisis_fail_threshold: float = -0.35,
    crisis_fail_penalty: float = 2.0,
) -> float:
    """
    Higher is better. Train = in-sample window, test = held-out OOS window.
    Crisis list: optional per-window metrics dicts (sharpe used); min Sharpe gets a bonus, very negative min → penalty.
    """
    if train_metrics.get("error") or test_metrics.get("error"):
        return -999.0

    s_tr = _safe_sharpe(train_metrics)
    s_te = _safe_sharpe(test_metrics)
    dd_tr = _safe_dd(train_metrics)
    dd_te = _safe_dd(test_metrics)

    score = w_oos_sharpe * s_te + w_is_sharpe * s_tr - w_dd * (dd_te + dd_tr)

    if crisis_metrics_list:
        sharpes = [_safe_sharpe(x) for x in crisis_metrics_list]
        min_c = min(sharpes) if sharpes else 0.0
        score += w_crisis_min * min_c
        if min_c < crisis_fail_threshold:
            score -= crisis_fail_penalty

    score -= parsimony_penalty
    return float(score)


def parsimony_penalty_vs_baseline(
    genome_attrs: Dict[str, Any],
    baseline_attrs: Dict[str, Any],
    *,
    cost_per_deviation: float = 0.05,
) -> float:
    """Penalize each gene that differs from canonical spec baseline (favor parsimony)."""
    n = 0
    for k, v in genome_attrs.items():
        if k not in baseline_attrs:
            continue
        b = baseline_attrs[k]
        if v != b:
            n += 1
    return float(n * cost_per_deviation)
