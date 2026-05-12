#!/usr/bin/env python3
"""
Full-window validation of best sweep row (trend_down) on cached klines.
Promotion gate: PF >= 2.0, expectancy >= 0.28R, trades >= 250 (trend_down fills).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.backtest_runner_cached import prepare_processed_frame, run_fast_trend_down_sweep_row
from scripts.analyze_trade_distribution import analyze_distribution, print_exit_reason_breakdown


def _filter_trend_down(trades: list) -> pd.DataFrame:
    df = pd.DataFrame(trades)
    if df.empty or "entry_regime_state" not in df.columns:
        return df
    return df[df["entry_regime_state"].astype(str) == "trend_down"].copy()


def validate_full_window(config_path: str | None = None) -> bool:
    sweep_path = Path(config_path) if config_path else (ROOT / "reports" / "pf_ablation_sweep_results.json")
    if not sweep_path.is_file():
        print("ERROR: Missing reports/pf_ablation_sweep_results.json — run scripts/fast_pf_sweep.py first")
        sys.exit(1)

    payload = json.loads(sweep_path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and ("best" in payload):
        best = payload.get("best") or {}
    else:
        best = payload if isinstance(payload, dict) else {}
    cfg = best.get("config") or best.get("params") or {}
    if not cfg:
        print("ERROR: best.config missing in sweep results")
        sys.exit(1)

    if not os.environ.get("BINANCE_KLINES_PARQUET", "").strip():
        p = ROOT / "data" / "klines_cache" / "btcusdt_15m.pkl"
        if p.is_file():
            os.environ["BINANCE_KLINES_PARQUET"] = str(p)

    os.environ["STRATEGY_SPEC_PATH"] = str((ROOT / "config" / "regime_isolation_v2.3.yaml").resolve())

    print("Full-window ICT + simulate (cached klines)...")
    processed = prepare_processed_frame()
    out = run_fast_trend_down_sweep_row(processed, trend_down_exits=cfg)
    td = _filter_trend_down(out.get("trades", []))
    if td.empty:
        print("REJECTED: no trend_down trades on full window")
        return False

    dist = analyze_distribution(td, output_path="reports/trade_distribution_full_validation.json", include_breakdown=True)
    print_exit_reason_breakdown(td)

    pf = float(dist.get("pf_actual", 0) or 0)
    exp = float(dist.get("expectancy_r", 0) or 0)
    n = int(dist.get("total_trades", 0) or 0)

    ok = pf >= 2.0 and exp >= 0.28 and n >= 250
    if ok:
        print("PROMOTED: full window passed PF>=2.0, Exp>=0.28R, trades>=250")
        stable = ROOT / "config" / "v2.5.2_stable.yaml"
        src = ROOT / "config" / "regime_isolation_v2.3.yaml"
        d = yaml.safe_load(src.read_text(encoding="utf-8"))
        d.setdefault("exits", {})
        td = dict(d["exits"].get("trend_down") or {})
        td.update(cfg)
        d["exits"]["trend_down"] = td
        header = (
            "# v2.5.2-stable — merged best sweep into exits.trend_down\n"
            "# STRATEGY_SPEC_PATH=config/v2.5.2_stable.yaml\n\n"
        )
        stable.write_text(
            header + yaml.dump(d, default_flow_style=False, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        print(f"Wrote {stable}")
        return True

    print(
        f"REJECTED: pf_actual={pf} (need>=2.0) expectancy_r={exp} (need>=0.28) trend_down_trades={n} (need>=250)"
    )
    return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(ROOT / "reports" / "pf_ablation_sweep_results.json"))
    args = parser.parse_args()
    ok = validate_full_window(config_path=args.config)
    sys.exit(0 if ok else 1)
