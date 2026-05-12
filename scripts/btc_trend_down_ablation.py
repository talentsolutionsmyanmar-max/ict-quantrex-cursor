#!/usr/bin/env python3
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
from core.config_loader import load_and_verify_config


def run_micro_ablation():
    cfg_yaml = load_and_verify_config("config/regime_isolation_v2.3.yaml", ["trading_universe", "exits"])

    # Force engine to use the isolated regime config.
    os.environ["STRATEGY_SPEC_PATH"] = str((ROOT / "config" / "regime_isolation_v2.3.yaml").resolve())

    if not os.environ.get("BINANCE_KLINES_PARQUET", "").strip():
        pkl = ROOT / "data" / "klines_cache" / "btcusdt_15m.pkl"
        parq = ROOT / "data" / "klines_cache" / "btcusdt_15m.parquet"
        if pkl.is_file():
            os.environ["BINANCE_KLINES_PARQUET"] = str(pkl)
        elif parq.is_file():
            os.environ["BINANCE_KLINES_PARQUET"] = str(parq)

    cfg = build_config()
    cfg.SYMBOL = "BTCUSDT"
    cfg.BACKTEST_START_DATE = "2024-01-01"
    cfg.BACKTEST_END_DATE = "2026-04-24"

    bt = Backtester(cfg, record_playbook=False)
    out = bt.run(verbose=False)
    trades = out.get("trades", [])
    trades_df = pd.DataFrame(trades)
    if "entry_regime_state" in trades_df.columns:
        trades_df = trades_df[trades_df["entry_regime_state"].astype(str) == "trend_down"].copy()
    reports_dir = ROOT / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / "btc_trend_down_trades.json").write_text(
        trades_df.to_json(orient="records"), encoding="utf-8"
    )

    if trades_df.empty:
        report = {
            "symbol": "BTCUSDT",
            "regime": "trend_down",
            "trades": 0,
            "pf": 0.0,
            "expectancy_r": 0.0,
            "status": "FAIL",
        }
        print(json.dumps(report, sort_keys=True))
        return report

    wins = trades_df[trades_df["pnl"] > 0]
    losses = trades_df[trades_df["pnl"] <= 0]
    win_pnl = float(wins["pnl"].sum()) if not wins.empty else 0.0
    loss_pnl = float(losses["pnl"].sum()) if not losses.empty else 0.0
    pf = (win_pnl / abs(loss_pnl)) if abs(loss_pnl) > 1e-12 else 999.0

    win_rate = float(len(wins) / len(trades_df))
    win_r = float(wins["r_multiple"].mean()) if not wins.empty else 0.0
    loss_r = float(abs(losses["r_multiple"].mean())) if not losses.empty else 0.0
    exp_r = (win_rate * win_r) - ((1.0 - win_rate) * loss_r)

    report = {
        "symbol": "BTCUSDT",
        "regime": "trend_down",
        "trades": int(len(trades_df)),
        "pf": round(float(pf), 3),
        "expectancy_r": round(float(exp_r), 3),
        "status": "PASS" if pf >= 1.3 and exp_r >= 0.25 and len(trades_df) >= 15 else "FAIL",
        "config_stop_loss_r": cfg_yaml.get("exits", {}).get("stop_loss_r"),
    }
    print(json.dumps(report, sort_keys=True))
    return report


if __name__ == "__main__":
    run_micro_ablation()
