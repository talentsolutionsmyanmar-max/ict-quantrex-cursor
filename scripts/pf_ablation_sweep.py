#!/usr/bin/env python3
from __future__ import annotations

import itertools
import json
import os
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backtester import Backtester
from config import build_config

PARAM_GRID = {
    "breakeven_at_r": [0.4, 0.5, 0.6],
    "trail_start_r": [1.8, 2.0, 2.2],
    "trail_distance_r": [0.7, 0.8, 0.9],
}


def _dist_from_df(trades_df: pd.DataFrame) -> dict:
    if trades_df.empty:
        return {"pf_actual": 0.0, "expectancy_r": 0.0, "total_trades": 0}
    wins = trades_df[trades_df["r_multiple"] > 0]
    losses = trades_df[trades_df["r_multiple"] <= 0]
    wr = len(wins) / len(trades_df) if len(trades_df) else 0.0
    avg_win = float(wins["r_multiple"].mean()) if not wins.empty else 0.0
    avg_loss = float(abs(losses["r_multiple"].mean())) if not losses.empty else 0.0
    pf = (len(wins) * avg_win) / (len(losses) * avg_loss) if len(losses) and avg_loss > 0 else 999.0
    exp = (wr * avg_win) - ((1.0 - wr) * avg_loss)
    return {"pf_actual": round(float(pf), 3), "expectancy_r": round(float(exp), 3), "total_trades": int(len(trades_df))}


def _run_one(base_df: pd.DataFrame, cfg: dict) -> dict:
    os.environ["STRATEGY_SPEC_PATH"] = str((ROOT / "config" / "regime_isolation_v2.3.yaml").resolve())
    run_cfg = build_config()
    run_cfg.SYMBOL = "BTCUSDT"
    run_cfg.BACKTEST_START_DATE = "2024-01-01"
    run_cfg.BACKTEST_END_DATE = "2026-04-24"
    if cfg.get("tp1_ratio", "__missing__") is None:
        run_cfg.TP1_RATIO = 99.0
    if cfg.get("trail_distance_r") is not None:
        run_cfg.TRAIL_ATR_MULTIPLIER = float(cfg["trail_distance_r"])
    bt = Backtester(run_cfg, record_playbook=False)
    bt._simulate_trades(base_df.copy(), verbose=False)
    trades_df = pd.DataFrame(bt.trades)
    if "entry_regime_state" in trades_df.columns:
        trades_df = trades_df[trades_df["entry_regime_state"].astype(str) == "trend_down"].copy()
    dist = _dist_from_df(trades_df)
    return {"config": cfg, **dist}


def run_pf_sweep():
    best = {"pf": 0.0, "config": None, "exp": None}
    all_rows = []
    keys = list(PARAM_GRID.keys())
    configs = [{k: v for k, v in zip(keys, combo)} for combo in itertools.product(*PARAM_GRID.values())]
    max_combos = int(os.getenv("PF_SWEEP_MAX_COMBOS", "0") or 0)
    if max_combos > 0:
        configs = configs[:max_combos]

    os.environ["STRATEGY_SPEC_PATH"] = str((ROOT / "config" / "regime_isolation_v2.3.yaml").resolve())
    base_cfg = build_config()
    base_cfg.SYMBOL = "BTCUSDT"
    base_cfg.BACKTEST_START_DATE = "2024-01-01"
    base_cfg.BACKTEST_END_DATE = "2026-04-24"
    base_bt = Backtester(base_cfg, record_playbook=False)
    df = base_bt.data_handler.fetch_historical_data(base_cfg.BACKTEST_START_DATE, base_cfg.BACKTEST_END_DATE)
    base_df = base_bt.ict.process_dataframe(df)

    for cfg in configs:
        row = _run_one(base_df, cfg)
        all_rows.append(row)
        if (
            float(row.get("pf_actual", 0.0)) > float(best["pf"])
            and float(row.get("expectancy_r", 0.0)) >= 0.60
            and int(row.get("total_trades", 0)) >= 300
        ):
            best = {"pf": float(row["pf_actual"]), "config": row["config"], "exp": float(row["expectancy_r"])}

    out_path = ROOT / "reports" / "pf_ablation_sweep_results.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"best": best, "results": all_rows}, indent=2), encoding="utf-8")
    print("OPTIMAL CONFIG FOUND:", json.dumps(best, sort_keys=True))
    return best


if __name__ == "__main__":
    run_pf_sweep()
