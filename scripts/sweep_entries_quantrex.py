#!/usr/bin/env python3
"""Run QuantRex backtest variants to increase entry throughput safely."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backtester import Backtester
from config import build_config


def _num(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _extract_metrics(result: Dict[str, Any]) -> Dict[str, Any]:
    metrics = result.get("metrics") if isinstance(result.get("metrics"), dict) else {}
    regime_summary = metrics.get("regime_summary") if isinstance(metrics.get("regime_summary"), dict) else {}
    unique_entries = regime_summary.get("unique_entries_total", metrics.get("total_trades", 0))
    return {
        "profit_factor": _num(metrics.get("profit_factor")),
        "max_drawdown": _num(metrics.get("max_drawdown")),
        "expectancy": _num(metrics.get("expectancy")),
        "total_trades": _num(metrics.get("total_trades")),
        "unique_entries": _num(unique_entries),
        "win_rate": _num(metrics.get("win_rate")),
        "total_pnl": _num(metrics.get("total_pnl")),
        "sharpe_ratio": _num(metrics.get("sharpe_ratio")),
    }


def _variant_rows() -> List[Dict[str, Any]]:
    return [
        {"name": "baseline", "overrides": {}},
        {"name": "loosen_strength_68", "overrides": {"MIN_SIGNAL_STRENGTH": 68}},
        {"name": "loosen_confluence_2", "overrides": {"MIN_CONFLUENCE": 2}},
        {"name": "loosen_both", "overrides": {"MIN_SIGNAL_STRENGTH": 68, "MIN_CONFLUENCE": 2}},
        {"name": "loosen_both_more", "overrides": {"MIN_SIGNAL_STRENGTH": 65, "MIN_CONFLUENCE": 2}},
    ]


def _render(rows: List[Dict[str, Any]]) -> str:
    header = (
        "variant".ljust(24)
        + "entries".rjust(10)
        + " PF".rjust(8)
        + " DD%".rjust(8)
        + " exp".rjust(10)
        + " pnl".rjust(12)
    )
    sep = "-" * len(header)
    out = [header, sep]
    for row in rows:
        m = row["metrics"]
        out.append(
            row["name"][:24].ljust(24)
            + f"{m['unique_entries']:.0f}".rjust(10)
            + f"{m['profit_factor']:.2f}".rjust(8)
            + f"{m['max_drawdown']:.2f}".rjust(8)
            + f"{m['expectancy']:.4f}".rjust(10)
            + f"{m['total_pnl']:.2f}".rjust(12)
        )
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description="Sweep entry-threshold variants for QuantRex.")
    ap.add_argument("--start-date", required=True)
    ap.add_argument("--end-date", required=True)
    ap.add_argument("--symbol", default="", help="Optional, defaults to current spec symbol")
    ap.add_argument("--timeframe", default="", help="Optional timeframe override")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    runs: List[Dict[str, Any]] = []
    for variant in _variant_rows():
        cfg = build_config()
        cfg.BACKTEST_START_DATE = args.start_date
        cfg.BACKTEST_END_DATE = args.end_date
        if args.symbol:
            cfg.SYMBOL = str(args.symbol).upper().replace("/", "")
        if args.timeframe:
            cfg.TIMEFRAME = str(args.timeframe)
        for k, v in variant["overrides"].items():
            setattr(cfg, k, v)

        result = Backtester(cfg).run(verbose=False)
        metrics = _extract_metrics(result)
        runs.append(
            {
                "name": variant["name"],
                "overrides": variant["overrides"],
                "metrics": metrics,
            }
        )

    ranked = sorted(runs, key=lambda r: (-r["metrics"]["unique_entries"], -r["metrics"]["profit_factor"], r["metrics"]["max_drawdown"]))
    print(_render(ranked))

    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "window": {"start": args.start_date, "end": args.end_date},
        "symbol": args.symbol or None,
        "timeframe": args.timeframe or None,
        "runs": ranked,
    }
    out_path = Path(args.out) if args.out else Path("reports") / "quantrex_entry_sweep.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nSaved sweep report: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
