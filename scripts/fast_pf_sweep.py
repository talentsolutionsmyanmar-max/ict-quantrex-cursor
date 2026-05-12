#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import time
from itertools import product
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.backtest_runner_cached import prepare_sweep_window, run_fast_trend_down_sweep_row
from scripts.analyze_trade_distribution import analyze_distribution

# v2.5.1 best baseline + v2.5.2 focused grid
SWEEP_DAYS = int(os.environ.get("PF_SWEEP_BACKTEST_DAYS", "180"))
MIN_TRADES = int(os.environ.get("PF_SWEEP_MIN_TRADES", "40"))
MIN_EXP = float(os.environ.get("PF_SWEEP_MIN_EXP_R", "0.28"))

BASE_TREND_DOWN = {
    "breakeven_at_r": 0.3,
    "trail_start_r": 2.0,
    "tp1_ratio": None,
}

PARAMS = {
    "breakeven_move_to_r": [0.05, 0.10, 0.15],
    "trail_distance_r": [0.8, 0.9, 1.0],
    "runner_allocation_pct": [0.0, 0.25, 0.30],
}


def _runner_trail(main_trail: float) -> float:
    return round(max(1.05, float(main_trail) + 0.15), 2)


def _filter_trend_down(trades: list):
    import pandas as pd

    df = pd.DataFrame(trades)
    if df.empty or "entry_regime_state" not in df.columns:
        return df
    return df[df["entry_regime_state"].astype(str) == "trend_down"].copy()


def run_fast_sweep() -> dict:
    t0 = time.time()
    os.environ["STRATEGY_SPEC_PATH"] = str((ROOT / "config" / "regime_isolation_v2.3.yaml").resolve())

    if not os.environ.get("BINANCE_KLINES_PARQUET", "").strip():
        default_p = ROOT / "data" / "klines_cache" / "btcusdt_15m.pkl"
        if not default_p.is_file():
            default_p = ROOT / "data" / "klines_cache" / "btcusdt_15m.parquet"
        if default_p.is_file():
            os.environ["BINANCE_KLINES_PARQUET"] = str(default_p)
        else:
            print("ERROR: Run python scripts/cache_klines.py first")
            sys.exit(1)

    processed = prepare_sweep_window(SWEEP_DAYS)
    results = []
    for be_move, trail_dist, runner_pct in product(
        PARAMS["breakeven_move_to_r"],
        PARAMS["trail_distance_r"],
        PARAMS["runner_allocation_pct"],
    ):
        row_cfg = {
            **BASE_TREND_DOWN,
            "breakeven_move_to_r": be_move,
            "trail_distance_r": trail_dist,
            "runner_allocation_pct": runner_pct,
            "runner_trail_distance_r": _runner_trail(trail_dist),
        }
        out = run_fast_trend_down_sweep_row(processed, trend_down_exits=row_cfg)
        td = _filter_trend_down(out.get("trades", []))
        if td.empty:
            dist = {
                "total_trades": 0,
                "win_rate": 0.0,
                "avg_win_r": 0.0,
                "avg_loss_r": 0.0,
                "payout_ratio": 0.0,
                "pf_actual": 0.0,
                "expectancy_r": 0.0,
                "exit_reason_counts": {},
                "exit_reason_breakdown": [],
            }
        else:
            dist = analyze_distribution(
                td,
                output_path="reports/trade_distribution_sweep_last.json",
                include_breakdown=True,
            )

        dist = dict(dist)
        dist["config"] = dict(row_cfg)
        results.append(dist)
        print(
            f"OK be_move={be_move} trail_dist={trail_dist} runner={runner_pct} | "
            f"PF={dist.get('pf_actual')} Exp={dist.get('expectancy_r')}R n={dist.get('total_trades', 0)}"
        )

    good = [
        d
        for d in results
        if int(d.get("total_trades", 0) or 0) >= MIN_TRADES
        and float(d.get("expectancy_r", -1) or 0) >= MIN_EXP
    ]
    best = (
        max(good, key=lambda x: float(x.get("pf_actual", 0) or 0))
        if good
        else (max(results, key=lambda x: float(x.get("pf_actual", 0) or 0)) if results else None)
    )

    elapsed = round(time.time() - t0, 2)
    out_path = Path("reports/pf_ablation_sweep_results.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(
            {
                "version": "v2.5.2",
                "sweep_days": SWEEP_DAYS,
                "elapsed_sec": elapsed,
                "best": best,
                "results": results,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\nBEST: {json.dumps(best, indent=2) if best else 'none'}")
    print(f"elapsed_sec={elapsed} (target <180s; reduce PF_SWEEP_BACKTEST_DAYS if needed)")
    print(f"Wrote {out_path.resolve()}")
    return best or {}


if __name__ == "__main__":
    run_fast_sweep()
