#!/usr/bin/env python3
"""Symbol-isolation gate: require PF >= threshold per symbol."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backtester import Backtester
from config import build_config


def _f(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def run_one_symbol(symbol: str, start_date: str, end_date: str, timeframe: str) -> Dict[str, Any]:
    cfg = build_config()
    cfg.SYMBOL = str(symbol).upper().replace("/", "")
    cfg.TIMEFRAME = str(timeframe)
    cfg.BACKTEST_START_DATE = str(start_date)
    cfg.BACKTEST_END_DATE = str(end_date)
    out = Backtester(cfg, record_playbook=False).run(verbose=False)
    m = out.get("metrics") if isinstance(out.get("metrics"), dict) else {}
    regime = m.get("regime_summary") if isinstance(m.get("regime_summary"), dict) else {}
    return {
        "symbol": cfg.SYMBOL,
        "profit_factor": _f(m.get("profit_factor")),
        "expectancy": _f(m.get("expectancy")),
        "max_drawdown": _f(m.get("max_drawdown")),
        "total_trades": int(_f(m.get("total_trades"))),
        "unique_entries": int(_f(regime.get("unique_entries_total"), _f(m.get("total_trades")))),
        "total_pnl": _f(m.get("total_pnl")),
        "win_rate": _f(m.get("win_rate")),
        "sharpe_ratio": _f(m.get("sharpe_ratio")),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Run per-symbol PF gate test.")
    ap.add_argument("--start-date", required=True)
    ap.add_argument("--end-date", required=True)
    ap.add_argument("--timeframe", default="15m")
    ap.add_argument("--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    ap.add_argument("--min-pf", type=float, default=1.3)
    ap.add_argument("--out", default="reports/symbol_pf_gate_latest.json")
    args = ap.parse_args()

    rows: List[Dict[str, Any]] = []
    for sym in args.symbols:
        row = run_one_symbol(sym, args.start_date, args.end_date, args.timeframe)
        row["pass_pf_gate"] = bool(row["profit_factor"] >= float(args.min_pf))
        rows.append(row)
        print(f"completed: {row['symbol']} pf={row['profit_factor']:.2f} pass={row['pass_pf_gate']}")

    all_pass = all(r["pass_pf_gate"] for r in rows)
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "window": {"start": args.start_date, "end": args.end_date, "timeframe": args.timeframe},
        "rule": f"symbol_pf >= {args.min_pf}",
        "all_pass": all_pass,
        "rows": rows,
    }
    p = Path(args.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"saved: {p}")
    if not all_pass:
        failing = [r["symbol"] for r in rows if not r["pass_pf_gate"]]
        print("failing symbols:", ", ".join(failing))
    return 0 if all_pass else 2


if __name__ == "__main__":
    raise SystemExit(main())
