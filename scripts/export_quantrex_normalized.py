#!/usr/bin/env python3
"""Export QuantRex /api/backtest output into normalized comparator JSON."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

import requests


def _num(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return float(default)


def _normalize(backtest_payload: Dict[str, Any], *, track: str, window: Dict[str, Any], request_payload: Dict[str, Any]) -> Dict[str, Any]:
    # /api/backtest returns either top-level metrics/trades or nested result in some contexts.
    root = backtest_payload.get("result") if isinstance(backtest_payload.get("result"), dict) else backtest_payload
    metrics = root.get("metrics") if isinstance(root, dict) else {}
    metrics = metrics if isinstance(metrics, dict) else {}

    regime_summary = metrics.get("regime_summary") if isinstance(metrics.get("regime_summary"), dict) else {}
    unique_entries = regime_summary.get("unique_entries_total")
    if unique_entries is None:
        unique_entries = metrics.get("unique_entries")
    if unique_entries is None:
        unique_entries = metrics.get("total_trades")

    normalized = {
        "track": track,
        "source": "quantrex_api_backtest",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "window": window,
        "request": request_payload,
        "metrics": {
            "profit_factor": _num(metrics.get("profit_factor")),
            "max_drawdown": _num(metrics.get("max_drawdown")),
            "expectancy": _num(metrics.get("expectancy")),
            "total_trades": _num(metrics.get("total_trades")),
            "unique_entries": _num(unique_entries),
            "win_rate": _num(metrics.get("win_rate")),
            "total_pnl": _num(metrics.get("total_pnl")),
            "sharpe_ratio": _num(metrics.get("sharpe_ratio")),
        },
        "meta": {
            "run_id": root.get("run_id") if isinstance(root, dict) else None,
            "api_success": bool(backtest_payload.get("success", True)),
        },
    }
    return normalized


def main() -> int:
    ap = argparse.ArgumentParser(description="Run QuantRex backtest via API and write normalized comparison JSON.")
    ap.add_argument("--base-url", default="http://127.0.0.1:5050")
    ap.add_argument("--track", default="quantrex_baseline")
    ap.add_argument("--start-date", required=True, help="YYYY-MM-DD")
    ap.add_argument("--end-date", required=True, help="YYYY-MM-DD")
    ap.add_argument("--symbol", default="")
    ap.add_argument("--timeframe", default="")
    ap.add_argument("--output", required=True, help="Path to normalized JSON output")
    ap.add_argument("--timeout-sec", type=float, default=120.0)
    args = ap.parse_args()

    request_payload: Dict[str, Any] = {"start_date": args.start_date, "end_date": args.end_date}
    if args.symbol:
        request_payload["symbol"] = str(args.symbol).upper().replace("/", "")
    if args.timeframe:
        request_payload["timeframe"] = str(args.timeframe)

    url = f"{args.base_url.rstrip('/')}/api/backtest"
    r = requests.post(url, json=request_payload, timeout=float(args.timeout_sec))
    r.raise_for_status()
    payload = r.json()
    if not isinstance(payload, dict):
        raise RuntimeError("Unexpected /api/backtest response shape")
    if payload.get("success") is False:
        raise RuntimeError(f"Backtest API error: {payload.get('error')}")

    window = {"start": args.start_date, "end": args.end_date}
    normalized = _normalize(payload, track=args.track, window=window, request_payload=request_payload)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(normalized, indent=2), encoding="utf-8")
    print(f"Wrote normalized track JSON: {out_path}")
    print(json.dumps(normalized.get("metrics", {}), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
