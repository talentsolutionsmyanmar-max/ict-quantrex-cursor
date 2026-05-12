#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.backtest_runner_cached import FastBacktester
from scripts.analyze_trade_distribution import analyze_distribution

CACHE_PATH = ROOT / "data" / "klines_cache" / "btcusdt_15m.pkl"
CHECKPOINT = ROOT / "data" / "sweep_checkpoint.json"
OUT_JSON = ROOT / "reports" / "pf_ablation_sweep_results.json"

GRID: List[Dict[str, Any]] = [
    {"breakeven_move_to_r": b, "trail_distance_r": t, "runner_allocation_pct": r}
    for b in [0.05, 0.10, 0.15]
    for t in [0.8, 0.9, 1.0]
    for r in [0.0, 0.25, 0.30]
]


def _parse_override_params(raw: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for token in [x.strip() for x in raw.split(";") if x.strip()]:
        p = [x.strip() for x in token.split(",")]
        if len(p) != 3:
            continue
        rows.append(
            {
                "breakeven_move_to_r": float(p[0]),
                "trail_distance_r": float(p[1]),
                "runner_allocation_pct": float(p[2]),
            }
        )
    return rows


def _combo_full_cfg(params: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "exits": {
            "trend_down": {
                "stop_loss_r": 1.0,
                "breakeven_at_r": 0.3,
                "trail_start_r": 2.0,
                "max_holding_hours": 36,
                **params,
            }
        }
    }


def run_resilient_sweep(limit: int | None = None, override_params: str | None = None):
    if not CACHE_PATH.exists():
        print("ERROR: Cache missing. Run scripts/cache_klines.py first.")
        sys.exit(1)

    os.environ.setdefault("BINANCE_KLINES_PARQUET", str(CACHE_PATH))
    sweep_days = int(os.environ.get("PF_SWEEP_BACKTEST_DAYS", "180"))

    grid = list(GRID)
    if override_params:
        parsed = _parse_override_params(override_params)
        if parsed:
            grid = parsed
    if limit is not None and limit > 0:
        grid = grid[: int(limit)]

    print(f"Starting {sweep_days}d sweep ({len(grid)} combos). Checkpoint: {CHECKPOINT}")
    runner = FastBacktester(str(CACHE_PATH))
    results: List[Dict[str, Any]] = []

    if CHECKPOINT.exists():
        try:
            results = json.loads(CHECKPOINT.read_text(encoding="utf-8"))
            if isinstance(results, list):
                print(f"Resuming from {len(results)} prior combos...")
            else:
                results = []
        except Exception:
            results = []

    for i, cfg in enumerate(grid):
        combo_id = f"combo_{i}"
        if any(str(r.get("id")) == combo_id for r in results):
            continue

        start = time.time()
        try:
            trades_df = runner.run_fast(_combo_full_cfg(cfg), days=sweep_days)
            if trades_df.empty:
                dist = {
                    "total_trades": 0,
                    "pf_actual": 0.0,
                    "expectancy_r": 0.0,
                    "exit_reason_counts": {},
                }
            else:
                dist = analyze_distribution(
                    trades_df,
                    output_path=str(ROOT / "reports" / "trade_distribution_sweep_last.json"),
                    include_breakdown=False,
                )
            dist = dict(dist)
            dist["id"] = combo_id
            dist["params"] = cfg
            results.append(dist)

            CHECKPOINT.parent.mkdir(parents=True, exist_ok=True)
            CHECKPOINT.write_text(json.dumps(results, indent=2), encoding="utf-8")
            elapsed = time.time() - start
            print(
                f"OK [{i+1}/{len(grid)}] be_buf={cfg['breakeven_move_to_r']:.2f} | "
                f"trail={cfg['trail_distance_r']:.1f} | runner={cfg['runner_allocation_pct']:.2f} | "
                f"PF={dist.get('pf_actual', 0):.2f} | Exp={dist.get('expectancy_r', 0):.3f}R | {elapsed:.0f}s"
            )
        except Exception as e:
            print(f"WARN Combo {i} failed: {str(e)[:120]}")

    if not results:
        print("ERROR: No combos completed.")
        return None

    ranked = sorted(
        results,
        key=lambda x: (float(x.get("pf_actual", 0) or 0), float(x.get("expectancy_r", 0) or 0)),
        reverse=True,
    )
    best = ranked[0]
    print(
        "\nTOP CONFIG:",
        json.dumps(
            {
                "id": best.get("id"),
                "params": best.get("params"),
                "pf_actual": best.get("pf_actual"),
                "expectancy_r": best.get("expectancy_r"),
            },
            indent=2,
        ),
    )
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(best, indent=2), encoding="utf-8")
    return best


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--override-params", type=str, default=None)
    args = parser.parse_args()
    run_resilient_sweep(limit=args.limit, override_params=args.override_params)
