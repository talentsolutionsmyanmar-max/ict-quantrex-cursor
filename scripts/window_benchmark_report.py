"""Controlled window metrics (same shape as reports/baseline_truth_v1.json)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict

import pandas as pd

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backtester import Backtester
from config import Config
from strategy.load_spec import apply_spec_to_config


def build_report(
    *,
    symbol: str,
    timeframe: str,
    start: str,
    end: str,
    capital: float,
) -> Dict[str, Any]:
    cfg = Config()
    apply_spec_to_config(cfg)
    cfg.SYMBOL = symbol
    cfg.TIMEFRAME = timeframe
    cfg.BACKTEST_START_DATE = start
    cfg.BACKTEST_END_DATE = end
    cfg.INITIAL_CAPITAL = float(capital)

    bt = Backtester(cfg, record_playbook=False)
    out = bt.run(verbose=False)
    m = out["metrics"]
    trades = out["trades"]
    if isinstance(m, dict) and m.get("error"):
        return {"error": m, "window": {"symbol": symbol, "timeframe": timeframe, "start": start, "end": end, "capital": capital}}

    rs = m.get("regime_summary") if isinstance(m, dict) else None
    depth: Dict[str, Any] = {}
    if isinstance(rs, dict):
        depth = {
            "unique_entries_total": rs.get("unique_entries_total"),
            "unique_entries_by_regime_state": rs.get("unique_entries_by_regime_state"),
            "pct_unique_entries_in_ranging": rs.get("pct_unique_entries_in_ranging"),
        }

    tdf = pd.DataFrame(trades)
    n = len(tdf)
    sl = tdf[tdf["exit_type"] == "STOP_LOSS"]
    tp1 = tdf[tdf["exit_type"] == "TP1"]
    tp2 = tdf[tdf["exit_type"] == "TP2"]
    tp3 = tdf[tdf["exit_type"] == "TP3"]

    by_regime: dict = {}
    if "entry_regime_state" in tdf.columns:
        for reg, g in tdf.groupby("entry_regime_state"):
            by_regime[str(reg)] = {
                "trades": int(len(g)),
                "expectancy": float(g["pnl"].mean()) if len(g) else 0.0,
                "max_drawdown_pnl": float(g["pnl"].min()) if len(g) else 0.0,
                "win_rate": float((g["pnl"] > 0).mean() * 100) if len(g) else 0.0,
            }

    return {
        "window": {
            "symbol": symbol,
            "timeframe": timeframe,
            "start": start,
            "end": end,
            "capital": float(capital),
        },
        "summary": {
            "closed_trades": n,
            "expectancy": round(float(m["expectancy"]), 2),
            "max_drawdown_pct": round(float(m["max_drawdown"]), 2),
            "win_rate": float(m["win_rate"]),
            "profit_factor": float(m["profit_factor"]),
        },
        "sl": {
            "count": int(len(sl)),
            "hit_rate_closed": float(len(sl) / n) if n else 0.0,
            "avg_sl_loss": float(sl["pnl"].mean()) if len(sl) else 0.0,
        },
        "tp": {
            "TP1": {
                "count": int(len(tp1)),
                "rate_closed": float(len(tp1) / n) if n else 0.0,
                "avg_pnl": float(tp1["pnl"].mean()) if len(tp1) else 0.0,
                "win_rate": float((tp1["pnl"] > 0).mean()) if len(tp1) else 0.0,
            },
            "TP2": {
                "count": int(len(tp2)),
                "rate_closed": float(len(tp2) / n) if n else 0.0,
                "avg_pnl": float(tp2["pnl"].mean()) if len(tp2) else 0.0,
                "win_rate": float((tp2["pnl"] > 0).mean()) if len(tp2) else 0.0,
            },
            "TP3": {
                "count": int(len(tp3)),
                "rate_closed": float(len(tp3) / n) if n else 0.0,
                "avg_pnl": float(tp3["pnl"].mean()) if len(tp3) else 0.0,
                "win_rate": float((tp3["pnl"] > 0).mean()) if len(tp3) else 0.0,
            },
        },
        "by_regime": by_regime,
        "entry_depth": depth,
    }


def _parse_batch(s: str) -> list[tuple[str, str, str]]:
    """
    Windows separated by ###. Each window: start,end[,label] (dates use YYYY-MM-DD).
    Example: 2024-01-01,2024-03-31,Q1###2024-04-01,2024-06-30,Q2
    """
    out: list[tuple[str, str, str]] = []
    for part in str(s).split("###"):
        part = part.strip()
        if not part:
            continue
        bits = [b.strip() for b in part.split(",") if b.strip()]
        if len(bits) < 2:
            raise ValueError(f"Invalid window segment (need start,end[,label]): {part!r}")
        start, end = bits[0], bits[1]
        label = bits[2] if len(bits) > 2 else f"{start}__{end}"
        out.append((start, end, label))
    return out


def main() -> None:
    p = argparse.ArgumentParser(description="Window benchmark JSON (validation sprint).")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--timeframe", default="15m")
    p.add_argument("--start", default="2024-04-01")
    p.add_argument("--end", default="2024-06-30")
    p.add_argument("--capital", type=float, default=10000.0)
    p.add_argument("--output", default="", help="Write JSON to this path (optional).")
    p.add_argument(
        "--batch",
        default="",
        help='Multiple windows separated by ###. Each: start,end,label — e.g. '
        '"2024-01-01,2024-03-31,Q1###2024-04-01,2024-06-30,Q2". When set, --start/--end ignored.',
    )
    args = p.parse_args()

    if args.batch:
        try:
            windows = _parse_batch(args.batch)
        except ValueError as e:
            print(json.dumps({"error": str(e)}))
            sys.exit(2)
        reps: list[Dict[str, Any]] = []
        for start, end, label in windows:
            one = build_report(
                symbol=args.symbol,
                timeframe=args.timeframe,
                start=start,
                end=end,
                capital=args.capital,
            )
            if isinstance(one, dict):
                one = dict(one)
                one["label"] = label
            reps.append(one)
        out_s = json.dumps({"batch": True, "windows": reps}, indent=2)
        # Zero-trade windows return {"error": ...}; still a valid validation outcome for batch sweeps.
        if any(isinstance(w, dict) and w.get("error") for w in reps):
            if args.output:
                Path(args.output).write_text(out_s, encoding="utf-8")
            print(out_s)
            sys.exit(0)
    else:
        rep = build_report(
            symbol=args.symbol,
            timeframe=args.timeframe,
            start=args.start,
            end=args.end,
            capital=args.capital,
        )
        if rep.get("error"):
            print(json.dumps(rep, indent=2))
            sys.exit(1)
        out_s = json.dumps(rep, indent=2)

    if args.output:
        Path(args.output).write_text(out_s, encoding="utf-8")
    print(out_s)


if __name__ == "__main__":
    main()
