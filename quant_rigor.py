"""
Chief-QE tooling: rolling stability sweep + friction (commission/slippage) stress on multi-coin runs.

Parallel capital model matches Backtester.run_multi (full INITIAL_CAPITAL per symbol).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from backtester import Backtester
from config import Config
from research_lab import apply_research_genes, clone_config_genes

# Heuristic gates (tune for your desk; documented in API response)
_STABILITY_MAX_MIN_SHARPE_SPAN = 0.22
_STABILITY_MAX_MIN_PF_SPAN = 0.45
_COST_STRESS_MIN_SHARPE_FLOOR = 0.15
_COST_STRESS_MIN_PF_FLOOR = 1.0


def equal_date_segments(start_date: str, end_date: str, n: int) -> List[Tuple[str, str]]:
    """Split [start_date, end_date] into n contiguous segments (inclusive)."""
    s = datetime.strptime(start_date, "%Y-%m-%d").date()
    e = datetime.strptime(end_date, "%Y-%m-%d").date()
    if n < 2 or e <= s:
        return [(start_date, end_date)]
    total_days = (e - s).days + 1
    if total_days < n * 14:
        return [(start_date, end_date)]
    base = max(14, total_days // n)
    out: List[Tuple[str, str]] = []
    cur: date = s
    for i in range(n):
        if i == n - 1:
            seg_end = e
        else:
            seg_end = min(cur + timedelta(days=base - 1), e)
        if seg_end < cur:
            break
        out.append((cur.isoformat(), seg_end.isoformat()))
        nxt = seg_end + timedelta(days=1)
        if nxt > e:
            break
        cur = nxt
    return out if len(out) >= 2 else [(start_date, end_date)]


def _agg_metrics(agg: Any) -> Dict[str, float]:
    if not isinstance(agg, dict):
        return {}
    def f(k: str, default: float = 0.0) -> float:
        try:
            return float(agg.get(k, default))
        except (TypeError, ValueError):
            return default
    return {
        "min_sharpe": f("min_sharpe"),
        "min_profit_factor": f("min_profit_factor"),
        "worst_max_drawdown_pct": f("worst_max_drawdown_pct"),
        "total_trades_all": f("total_trades_all"),
    }


def run_stability_sweep(
    runner: Config,
    *,
    symbols: List[str],
    timeframe: str,
    start_date: str,
    end_date: str,
    n_windows: int = 3,
    max_workers: int = 1,
) -> Dict[str, Any]:
    """
    Run Backtester.run_multi on each contiguous date segment.
    """
    n_windows = max(2, min(int(n_windows), 8))
    segs = equal_date_segments(start_date, end_date, n_windows)
    clean_syms = [str(x).upper().replace("/", "") for x in symbols if x]
    if not clean_syms:
        return {"success": False, "error": "no symbols", "windows": []}

    rows: List[Dict[str, Any]] = []
    for i, (ws, we) in enumerate(segs):
        c = clone_config_genes(runner)
        c.TIMEFRAME = timeframe
        c.INITIAL_CAPITAL = float(runner.INITIAL_CAPITAL)
        r = Backtester.run_multi(
            base_config=c,
            symbols=clean_syms,
            timeframe=timeframe,
            start_date=ws,
            end_date=we,
            initial_capital=float(runner.INITIAL_CAPITAL),
            max_workers=max_workers,
            verbose=False,
        )
        ok = bool(r.get("success"))
        agg = r.get("aggregate") if ok else {}
        rows.append(
            {
                "segment": i + 1,
                "start": ws,
                "end": we,
                "success": ok,
                "error": r.get("error"),
                "aggregate": agg,
                "metrics": _agg_metrics(agg),
            }
        )

    ok_rows = [x for x in rows if x.get("success") and x.get("metrics")]
    mss = [x["metrics"]["min_sharpe"] for x in ok_rows]
    mpf = [x["metrics"]["min_profit_factor"] for x in ok_rows]

    span_s = (max(mss) - min(mss)) if len(mss) > 1 else 0.0
    span_pf = (max(mpf) - min(mpf)) if len(mpf) > 1 else 0.0

    all_ok = all(x.get("success") for x in rows)
    stable_s = span_s <= _STABILITY_MAX_MIN_SHARPE_SPAN
    stable_pf = span_pf <= _STABILITY_MAX_MIN_PF_SPAN

    if not all_ok:
        verdict = "FAIL"
    elif not stable_s or not stable_pf:
        verdict = "WARN"
    else:
        verdict = "PASS"

    return {
        "success": True,
        "verdict": verdict,
        "segments_requested": n_windows,
        "segments_used": len(segs),
        "symbols": clean_syms,
        "thresholds": {
            "max_min_sharpe_span": _STABILITY_MAX_MIN_SHARPE_SPAN,
            "max_min_profit_factor_span": _STABILITY_MAX_MIN_PF_SPAN,
            "observed_min_sharpe_span": round(span_s, 4),
            "observed_min_profit_factor_span": round(span_pf, 4),
        },
        "windows": rows,
    }


def run_cost_stress(
    runner: Config,
    *,
    symbols: List[str],
    timeframe: str,
    start_date: str,
    end_date: str,
    friction_mult: float = 1.5,
    max_workers: int = 1,
) -> Dict[str, Any]:
    """
    Baseline run_multi vs same with COMMISSION and SLIPPAGE scaled by friction_mult (capped).
    """
    clean_syms = [str(x).upper().replace("/", "") for x in symbols if x]
    if not clean_syms:
        return {"success": False, "error": "no symbols"}

    friction_mult = max(1.0, min(float(friction_mult), 4.0))

    def _run(c: Config) -> Dict[str, Any]:
        cc = clone_config_genes(c)
        cc.TIMEFRAME = timeframe
        return Backtester.run_multi(
            base_config=cc,
            symbols=clean_syms,
            timeframe=timeframe,
            start_date=start_date,
            end_date=end_date,
            initial_capital=float(runner.INITIAL_CAPITAL),
            max_workers=max_workers,
            verbose=False,
        )

    baseline_cfg = clone_config_genes(runner)
    r0 = _run(baseline_cfg)
    if not r0.get("success"):
        return {
            "success": False,
            "error": "baseline_multi_failed",
            "baseline": {"success": False, "error": r0.get("error"), "aggregate": r0.get("aggregate")},
        }

    stressed_cfg = clone_config_genes(runner)
    stressed_cfg.COMMISSION = min(0.08, float(runner.COMMISSION) * friction_mult)
    stressed_cfg.SLIPPAGE = min(0.02, float(runner.SLIPPAGE) * friction_mult)
    r1 = _run(stressed_cfg)

    m0 = _agg_metrics(r0.get("aggregate") if r0.get("success") else {})
    m1 = _agg_metrics(r1.get("aggregate") if r1.get("success") else {})

    drop_s = m0.get("min_sharpe", 0) - m1.get("min_sharpe", 0)

    pass_stress = True
    reasons: List[str] = []
    if not r1.get("success"):
        pass_stress = False
        reasons.append("stressed_run_failed")
    if m1.get("min_sharpe", 0) < _COST_STRESS_MIN_SHARPE_FLOOR:
        pass_stress = False
        reasons.append("stressed_min_sharpe_below_floor")
    if m1.get("min_profit_factor", 0) < _COST_STRESS_MIN_PF_FLOOR:
        pass_stress = False
        reasons.append("stressed_min_pf_below_floor")
    if drop_s > 0.35:
        reasons.append("large_sharpe_drop_under_stress")

    verdict = "PASS" if pass_stress and not reasons else ("WARN" if pass_stress else "FAIL")

    return {
        "success": True,
        "verdict": verdict,
        "friction_mult": friction_mult,
        "commission_baseline": float(baseline_cfg.COMMISSION),
        "slippage_baseline": float(baseline_cfg.SLIPPAGE),
        "commission_stressed": float(stressed_cfg.COMMISSION),
        "slippage_stressed": float(stressed_cfg.SLIPPAGE),
        "baseline": {
            "success": r0.get("success"),
            "error": r0.get("error"),
            "aggregate": r0.get("aggregate"),
            "metrics": m0,
        },
        "stressed": {
            "success": r1.get("success"),
            "error": r1.get("error"),
            "aggregate": r1.get("aggregate"),
            "metrics": m1,
        },
        "delta_min_sharpe": round(drop_s, 4),
        "floors": {"min_sharpe": _COST_STRESS_MIN_SHARPE_FLOOR, "min_profit_factor": _COST_STRESS_MIN_PF_FLOOR},
        "flags": reasons,
    }


def build_runner_from_lab(
    *,
    runtime_cfg: Config,
    genes: Optional[Dict[str, Any]] = None,
) -> Config:
    """Clone runtime genes, optionally override with evolved genes."""
    runner = clone_config_genes(runtime_cfg)
    if isinstance(genes, dict) and genes:
        errs = apply_research_genes(runner, genes)
        if errs:
            raise ValueError("gene apply: " + "; ".join(errs))
    return runner
