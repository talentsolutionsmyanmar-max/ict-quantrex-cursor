#!/usr/bin/env python3
"""Sweep entry thresholds via QuantRex backtest API overrides."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import requests


def _num(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _extract_metrics(resp: Dict[str, Any]) -> Dict[str, Any]:
    metrics = resp.get("metrics") if isinstance(resp.get("metrics"), dict) else {}
    regime = metrics.get("regime_summary") if isinstance(metrics.get("regime_summary"), dict) else {}
    unique_entries = _num(regime.get("unique_entries_total"), _num(metrics.get("total_trades")))
    return {
        "profit_factor": _num(metrics.get("profit_factor")),
        "max_drawdown": _num(metrics.get("max_drawdown")),
        "expectancy": _num(metrics.get("expectancy")),
        "total_trades": _num(metrics.get("total_trades")),
        "unique_entries": unique_entries,
        "win_rate": _num(metrics.get("win_rate")),
        "total_pnl": _num(metrics.get("total_pnl")),
        "sharpe_ratio": _num(metrics.get("sharpe_ratio")),
    }


def _variants() -> List[Dict[str, Any]]:
    return [
        {"name": "baseline", "min_signal_strength": None, "min_confluence": None},
        {"name": "sig68_conf3", "min_signal_strength": 68, "min_confluence": 3},
        {"name": "sig72_conf2", "min_signal_strength": 72, "min_confluence": 2},
        {"name": "sig68_conf2", "min_signal_strength": 68, "min_confluence": 2},
        {"name": "sig65_conf2", "min_signal_strength": 65, "min_confluence": 2},
    ]


def _render(rows: List[Dict[str, Any]]) -> str:
    header = (
        "variant".ljust(16)
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
            row["name"].ljust(16)
            + f"{m['unique_entries']:.0f}".rjust(10)
            + f"{m['profit_factor']:.2f}".rjust(8)
            + f"{m['max_drawdown']:.2f}".rjust(8)
            + f"{m['expectancy']:.4f}".rjust(10)
            + f"{m['total_pnl']:.2f}".rjust(12)
        )
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description="Run entry threshold sweep through /api/backtest.")
    ap.add_argument("--base-url", default="http://127.0.0.1:5050")
    ap.add_argument("--start-date", required=True)
    ap.add_argument("--end-date", required=True)
    ap.add_argument("--symbol", default="")
    ap.add_argument("--timeframe", default="")
    ap.add_argument("--out", default="reports/quantrex_entry_sweep_api.json")
    args = ap.parse_args()

    rows: List[Dict[str, Any]] = []
    for v in _variants():
        payload: Dict[str, Any] = {
            "start_date": args.start_date,
            "end_date": args.end_date,
        }
        if args.symbol:
            payload["symbol"] = args.symbol
        if args.timeframe:
            payload["timeframe"] = args.timeframe
        if v["min_signal_strength"] is not None:
            payload["min_signal_strength"] = v["min_signal_strength"]
        if v["min_confluence"] is not None:
            payload["min_confluence"] = v["min_confluence"]

        r = requests.post(f"{args.base_url.rstrip('/')}/api/backtest", json=payload, timeout=180)
        r.raise_for_status()
        out = r.json()
        if not out.get("success"):
            raise RuntimeError(f"Variant {v['name']} failed: {out.get('error')}")
        metrics = _extract_metrics(out)
        rows.append({"name": v["name"], "payload": payload, "metrics": metrics})
        print(f"completed: {v['name']}")

    ranked = sorted(rows, key=lambda x: (-x["metrics"]["unique_entries"], -x["metrics"]["profit_factor"], x["metrics"]["max_drawdown"]))
    print("\n" + _render(ranked))

    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "window": {"start": args.start_date, "end": args.end_date},
        "rows": ranked,
    }
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nsaved: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
