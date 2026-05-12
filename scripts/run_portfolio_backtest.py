#!/usr/bin/env python3
"""Run mandatory portfolio path backtest (multi-symbol) and write summary."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backtester import Backtester
from config import build_config


def _strip_frames(out: dict) -> dict:
    cleaned = dict(out)
    per_symbol = cleaned.get("per_symbol")
    if isinstance(per_symbol, dict):
        ps2 = {}
        for sym, payload in per_symbol.items():
            if not isinstance(payload, dict):
                ps2[sym] = payload
                continue
            d = dict(payload)
            d.pop("df", None)
            d.pop("equity_curve", None)
            ps2[sym] = d
        cleaned["per_symbol"] = ps2
    return cleaned


def main() -> int:
    ap = argparse.ArgumentParser(description="Run portfolio backtest via Backtester.run_multi")
    ap.add_argument("--start-date", required=True)
    ap.add_argument("--end-date", required=True)
    ap.add_argument("--timeframe", default="15m")
    ap.add_argument("--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    ap.add_argument("--initial-capital", type=float, default=10000.0)
    ap.add_argument("--out", default="reports/portfolio_backtest_latest.json")
    args = ap.parse_args()

    cfg = build_config()
    out = Backtester.run_multi(
        base_config=cfg,
        symbols=[str(s).upper().replace("/", "") for s in args.symbols],
        timeframe=str(args.timeframe),
        start_date=str(args.start_date),
        end_date=str(args.end_date),
        initial_capital=float(args.initial_capital),
        verbose=False,
    )
    out_clean = _strip_frames(out)
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "request": {
            "start_date": args.start_date,
            "end_date": args.end_date,
            "timeframe": args.timeframe,
            "symbols": args.symbols,
            "initial_capital": args.initial_capital,
        },
        "result": out_clean,
    }
    p = Path(args.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"saved: {p}")
    print(json.dumps(out_clean.get("aggregate", out_clean), indent=2))
    return 0 if out_clean.get("success") else 2


if __name__ == "__main__":
    raise SystemExit(main())
