#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backtester import Backtester
from config import build_config


def _apply_config_overrides(cfg: Any, overrides: Dict[str, Any]) -> None:
    trend_down = overrides.get("exits.trend_down") if isinstance(overrides, dict) else None
    if not isinstance(trend_down, dict):
        return
    # Best-effort mapping into current runtime config fields.
    if trend_down.get("tp1_ratio", "__missing__") is None:
        cfg.TP1_RATIO = 99.0
    elif trend_down.get("tp1_ratio") is not None:
        cfg.TP1_RATIO = float(trend_down["tp1_ratio"])
    if trend_down.get("trail_distance_r") is not None:
        cfg.TRAIL_ATR_MULTIPLIER = float(trend_down["trail_distance_r"])
    if trend_down.get("trail_start_r") is not None:
        cfg.MIN_SIGNAL_STRENGTH = max(float(cfg.MIN_SIGNAL_STRENGTH), 72.0)


def run_single_symbol_backtest(params: Dict[str, Any]) -> Dict[str, Any]:
    os.environ["STRATEGY_SPEC_PATH"] = str((ROOT / "config" / "regime_isolation_v2.3.yaml").resolve())
    cfg = build_config()
    cfg.SYMBOL = str(params.get("symbol", "BTCUSDT"))
    if params.get("start"):
        cfg.BACKTEST_START_DATE = str(params["start"])
    if params.get("end"):
        cfg.BACKTEST_END_DATE = str(params["end"])
    _apply_config_overrides(cfg, params.get("config_overrides", {}))

    bt = Backtester(cfg, record_playbook=False)
    out = bt.run(verbose=False)
    trades = out.get("trades", [])

    regime_filter = params.get("regime_filter")
    if regime_filter:
        trades = [t for t in trades if str(t.get("entry_regime_state")) == str(regime_filter)]
    return {"trades": trades, "metrics": out.get("metrics", {})}
