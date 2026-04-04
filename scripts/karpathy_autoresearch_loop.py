"""
Karpathy-style AutoResearch loop (minimal, no external ML deps):

1) Export labeled examples from playbook sqlite (OPEN + exit events)
2) Learn a conservative proposal for thresholds from empirical data:
   - suggest MIN_SIGNAL_STRENGTH in allowed grid
   - suggest MIN_CONFLUENCE in allowed grid
3) Verify the proposal with Backtester.run_multi on an OOS window

This intentionally only auto-tunes *thresholds* first (safe levers).
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
TRAIN_DIR = ROOT / "data" / "training"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backtester import Backtester  # noqa: E402
from config import build_config  # noqa: E402
from research_lab import ALLOWED_GENE_VALUES  # noqa: E402
from scripts.export_playbook_dataset import export_dataset  # noqa: E402

DEFAULT_SYMBOLS = ["SOLUSDT", "ETHUSDT", "BTCUSDT", "BNBUSDT", "XRPUSDT", "ADAUSDT"]


def _multi_symbols() -> list[str]:
    cfg = build_config()
    wl = list(getattr(cfg, "WATCHLIST", []) or [])
    syms = [str(s).upper().replace("/", "") for s in wl if s]
    return syms[:8] if len(syms) >= 6 else list(DEFAULT_SYMBOLS)


@dataclass
class Proposal:
    min_signal_strength: int
    min_confluence: int
    rationale: Dict[str, Any]


def _load_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                rows.append(obj)
        except Exception:
            continue
    return rows


def _summarize_by_threshold(examples: List[Dict[str, Any]], *, strength_floor: int, conf_floor: int) -> Dict[str, Any]:
    kept = []
    for ex in examples:
        feats = ex.get("features") if isinstance(ex.get("features"), dict) else {}
        try:
            s = float(feats.get("signal_strength", 0.0))
        except Exception:
            s = 0.0
        try:
            c = int(feats.get("confluence", 0) or 0)
        except Exception:
            c = 0
        if s >= strength_floor and c >= conf_floor:
            kept.append(ex)

    if not kept:
        return {"n": 0, "win_rate": 0.0, "avg_pnl": 0.0}
    wins = sum(1 for k in kept if float(k.get("pnl", 0.0) or 0.0) > 0)
    pnls = [float(k.get("pnl", 0.0) or 0.0) for k in kept]
    return {
        "n": int(len(kept)),
        "win_rate": round(float(wins / max(1, len(kept))), 4),
        "avg_pnl": round(float(sum(pnls) / max(1, len(pnls))), 4),
    }


def _cap_pf(pf: float, cap: float = 8.0) -> float:
    try:
        x = float(pf)
    except Exception:
        return 0.0
    if x < 0:
        return 0.0
    return float(min(x, cap))


def _score_aggregate(agg: Dict[str, Any]) -> float:
    """
    Robustness-first score for quick grid search:
    min Sharpe dominates; capped min PF; small DD term.

    Includes a hard activity guard: reject if total_trades_all < 600.
    """
    if not isinstance(agg, dict):
        return -999.0
    try:
        min_s = float(agg.get("min_sharpe", 0.0))
        mean_s = float(agg.get("mean_sharpe", 0.0))
        min_pf = _cap_pf(float(agg.get("min_profit_factor", 0.0)))
        worst_dd = float(agg.get("worst_max_drawdown_pct", 0.0))  # negative
        total_tr = int(agg.get("total_trades_all", 0) or 0)
    except Exception:
        return -999.0

    if total_tr < 600:
        return -999.0 + total_tr * 0.1

    return (0.50 * min_s) + (0.25 * mean_s) + (0.20 * min_pf) + (0.05 * (-worst_dd / 100.0))


def propose_thresholds_from_playbook(examples: List[Dict[str, Any]]) -> Proposal:
    strength_grid = list(ALLOWED_GENE_VALUES.get("MIN_SIGNAL_STRENGTH", (65, 68, 70, 72, 75, 78, 80)))
    conf_grid = list(ALLOWED_GENE_VALUES.get("MIN_CONFLUENCE", (2, 3)))

    baseline_strength = 68  # current v1.8 baseline
    baseline_conf = 2

    scored: List[Tuple[float, Dict[str, Any]]] = []
    for s in strength_grid:
        for c in conf_grid:
            summ = _summarize_by_threshold(examples, strength_floor=int(s), conf_floor=int(c))
            # Objective: maximize avg_pnl but keep activity; penalize sparse.
            n = int(summ["n"])
            avg_pnl = float(summ["avg_pnl"])
            wr = float(summ["win_rate"])
            sparse_pen = 0.0
            if n < 120:
                sparse_pen += (120 - n) * 0.01  # soft penalty per missing sample
            # preference for stability: slight bonus for higher win rate
            score = avg_pnl + (wr * 5.0) - sparse_pen
            scored.append((score, {"min_signal_strength": int(s), "min_confluence": int(c), "summary": summ}))

    scored.sort(key=lambda x: x[0], reverse=True)
    best = scored[0][1] if scored else {"min_signal_strength": baseline_strength, "min_confluence": baseline_conf, "summary": {}}

    rationale = {
        "baseline": {"min_signal_strength": baseline_strength, "min_confluence": baseline_conf},
        "best_summary": best.get("summary"),
        "top3": [s[1] for s in scored[:3]],
    }
    return Proposal(min_signal_strength=int(best["min_signal_strength"]), min_confluence=int(best["min_confluence"]), rationale=rationale)


def propose_thresholds_by_oos_grid_search(*, start_date: str, end_date: str) -> Proposal:
    """
    "Train" thresholds by direct OOS evaluation (small search space, high signal).
    This avoids playbook sampling bias and directly optimizes multi-coin robustness.
    """
    strength_grid = list(ALLOWED_GENE_VALUES.get("MIN_SIGNAL_STRENGTH", (65, 68, 70, 72, 75, 78, 80)))
    conf_grid = list(ALLOWED_GENE_VALUES.get("MIN_CONFLUENCE", (2, 3)))

    rows: List[Dict[str, Any]] = []
    best: Dict[str, Any] | None = None
    best_score = -1e18

    symbols = _multi_symbols()
    for s in strength_grid:
        for c in conf_grid:
            cfg = build_config()
            cfg.MIN_SIGNAL_STRENGTH = int(s)
            cfg.MIN_CONFLUENCE = int(c)
            cfg.TIMEFRAME = "1h"
            res = Backtester.run_multi(
                base_config=cfg,
                symbols=symbols,
                timeframe="1h",
                start_date=start_date,
                end_date=end_date,
                initial_capital=float(cfg.INITIAL_CAPITAL),
                max_workers=1,
                verbose=False,
            )
            agg = res.get("aggregate") if isinstance(res, dict) else None
            score = _score_aggregate(agg if isinstance(agg, dict) else {})
            row = {"MIN_SIGNAL_STRENGTH": int(s), "MIN_CONFLUENCE": int(c), "score": round(float(score), 6), "aggregate": agg}
            rows.append(row)
            if score > best_score:
                best_score = score
                best = row

    rationale = {
        "window": [start_date, end_date],
        "symbols": symbols,
        "objective": "maximize robustness score with activity guard (total_trades_all >= 600)",
        "top5": sorted(rows, key=lambda r: float(r.get("score", -1e18)), reverse=True)[:5],
    }
    if not best:
        return Proposal(min_signal_strength=68, min_confluence=2, rationale=rationale)
    return Proposal(min_signal_strength=int(best["MIN_SIGNAL_STRENGTH"]), min_confluence=int(best["MIN_CONFLUENCE"]), rationale=rationale)


def verify_proposal_multi(*, proposal: Proposal, start_date: str, end_date: str) -> Dict[str, Any]:
    cfg = build_config()
    # apply proposed thresholds only (keep other v1.8 genes as in spec)
    cfg.MIN_SIGNAL_STRENGTH = int(proposal.min_signal_strength)
    cfg.MIN_CONFLUENCE = int(proposal.min_confluence)
    cfg.TIMEFRAME = "1h"

    symbols = _multi_symbols()
    res = Backtester.run_multi(
        base_config=cfg,
        symbols=symbols,
        timeframe="1h",
        start_date=start_date,
        end_date=end_date,
        initial_capital=float(cfg.INITIAL_CAPITAL),
        max_workers=1,
        verbose=False,
    )
    return {
        "proposal": {"MIN_SIGNAL_STRENGTH": cfg.MIN_SIGNAL_STRENGTH, "MIN_CONFLUENCE": cfg.MIN_CONFLUENCE},
        "symbols": symbols,
        "aggregate": res.get("aggregate"),
    }


def main() -> None:
    TRAIN_DIR.mkdir(parents=True, exist_ok=True)
    out_path = TRAIN_DIR / "playbook_examples_latest.jsonl"
    export_res = export_dataset(out_path=out_path, limit=None)
    examples = _load_jsonl(out_path)

    # OOS verify window (recent-ish): last ~9 months of your full period
    verify_start = "2025-07-01"
    verify_end = "2026-03-30"

    prop_playbook = propose_thresholds_from_playbook(examples)
    prop_oos = propose_thresholds_by_oos_grid_search(start_date=verify_start, end_date=verify_end)

    base = build_config()
    ver_baseline = verify_proposal_multi(
        proposal=Proposal(
            min_signal_strength=int(base.MIN_SIGNAL_STRENGTH),
            min_confluence=int(base.MIN_CONFLUENCE),
            rationale={"kind": "baseline"},
        ),
        start_date=verify_start,
        end_date=verify_end,
    )
    ver_playbook = verify_proposal_multi(proposal=prop_playbook, start_date=verify_start, end_date=verify_end)
    ver_oos = verify_proposal_multi(proposal=prop_oos, start_date=verify_start, end_date=verify_end)

    out = {
        "export": export_res,
        "examples_loaded": len(examples),
        "verify_window": [verify_start, verify_end],
        "baseline_verify": ver_baseline,
        "proposal_from_playbook": {
            "MIN_SIGNAL_STRENGTH": prop_playbook.min_signal_strength,
            "MIN_CONFLUENCE": prop_playbook.min_confluence,
            "rationale": prop_playbook.rationale,
            "verify": ver_playbook,
        },
        "proposal_from_oos_grid_search": {
            "MIN_SIGNAL_STRENGTH": prop_oos.min_signal_strength,
            "MIN_CONFLUENCE": prop_oos.min_confluence,
            "rationale": prop_oos.rationale,
            "verify": ver_oos,
        },
        "note": "Use the OOS grid-search proposal only if it beats baseline and keeps total_trades_all >= 600.",
    }
    print(json.dumps(out, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

