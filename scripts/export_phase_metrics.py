#!/usr/bin/env python3
"""
Export Phase A/B metrics (control vs hybrid) for paper-run comparison.

Default trade log: ``logs/paper_trades.jsonl`` if present, else
``data/paper_trades_fallback.jsonl`` (Supabase fallback from ``log_trade_to_supabase``).

Default equity: ``logs/equity_curve.csv`` if present (optional; live loop may not write it yet).

Usage (from ``ict-quantrex-cursor`` root)::

    python scripts/export_phase_metrics.py --phase control --days 3 --output reports/phaseA_metrics.json
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


def _first_existing(paths: List[Path]) -> Optional[Path]:
    for p in paths:
        if p.is_file():
            return p
    return None


def _load_trades_jsonl(path: Path) -> pd.DataFrame:
    if not path.is_file():
        return pd.DataFrame()
    try:
        return pd.read_json(path, lines=True)
    except (ValueError, OSError):
        return pd.DataFrame()


def _load_equity_csv(path: Path) -> pd.DataFrame:
    if not path.is_file():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except (ValueError, OSError):
        return pd.DataFrame()


def _pnl_series(trades: pd.DataFrame) -> pd.Series:
    for col in ("pnl_usd", "pnl"):
        if col in trades.columns:
            return pd.to_numeric(trades[col], errors="coerce").fillna(0.0)
    return pd.Series(dtype="float64")


def _filter_trades_by_days(trades: pd.DataFrame, days: int) -> pd.DataFrame:
    if trades.empty or days <= 0:
        return trades
    ts_col = None
    for c in ("timestamp", "exit_time", "ts"):
        if c in trades.columns:
            ts_col = c
            break
    if ts_col is None:
        return trades
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    ts = pd.to_datetime(trades[ts_col], utc=True, errors="coerce")
    return trades.loc[ts > pd.Timestamp(cutoff)].copy()


def _filter_equity_by_days(equity: pd.DataFrame, days: int) -> pd.DataFrame:
    if equity.empty or days <= 0:
        return equity
    ts_col = None
    for c in ("timestamp", "ts", "time"):
        if c in equity.columns:
            ts_col = c
            break
    if ts_col is None:
        return equity
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    ts = pd.to_datetime(equity[ts_col], utc=True, errors="coerce")
    return equity.loc[ts > pd.Timestamp(cutoff)].copy()


def _equity_values(equity: pd.DataFrame) -> Tuple[Optional[str], Optional[pd.Series]]:
    for col in ("equity", "value", "portfolio_value", "balance"):
        if col in equity.columns:
            return col, pd.to_numeric(equity[col], errors="coerce")
    return None, None


def _max_drawdown_from_equity(series: pd.Series) -> Optional[float]:
    s = series.dropna()
    if s.empty:
        return None
    vals = s.astype("float64").values
    peak = pd.Series(vals).cummax().values
    dd = (peak - vals) / peak.clip(lower=1e-12)
    return float(dd.max())


def _summarize_hook_log(path: Path, days: int) -> Dict[str, Any]:
    if not path.is_file():
        return {"hook_log_path": str(path), "present": False}
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    events: Dict[str, int] = {}
    errors = 0
    last_lines = 0
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            last_lines += 1
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                errors += 1
                continue
            ts_raw = row.get("timestamp")
            if ts_raw:
                t = pd.to_datetime(ts_raw, utc=True, errors="coerce")
                if pd.isna(t) or t.to_pydatetime() <= cutoff:
                    continue
            ev = str(row.get("event") or row.get("kind") or "unknown")
            events[ev] = events.get(ev, 0) + 1
    return {
        "hook_log_path": str(path),
        "present": True,
        "events_in_window": events,
        "parse_errors": errors,
        "note": "Counts only lines with parseable JSON in the time window (timestamp when present).",
    }


def compute_metrics(
    trades_path: Optional[Path],
    equity_path: Optional[Path],
    days: int,
    phase: str,
    hook_log: Optional[Path],
) -> Dict[str, Any]:
    trades_df = _load_trades_jsonl(trades_path) if trades_path else pd.DataFrame()
    trades_df = _filter_trades_by_days(trades_df, days)

    pnl = _pnl_series(trades_df)
    total_trades = int(len(trades_df))
    if total_trades > 0:
        win_trades = int((pnl > 0).sum())
        gross_profit = float(pnl[pnl > 0].sum()) if (pnl > 0).any() else 0.0
        gross_loss = float(abs(pnl[pnl < 0].sum())) if (pnl < 0).any() else 0.0
        win_rate = win_trades / total_trades
        if gross_loss > 0:
            profit_factor = gross_profit / gross_loss
        else:
            profit_factor = None
    else:
        win_trades = 0
        gross_profit = 0.0
        gross_loss = 0.0
        win_rate = 0.0
        profit_factor = None

    equity_df = _load_equity_csv(equity_path) if equity_path else pd.DataFrame()
    equity_df = _filter_equity_by_days(equity_df, days)

    max_dd: Optional[float] = None
    final_equity: Optional[float] = None
    equity_col_used: Optional[str] = None

    if not equity_df.empty:
        if "drawdown_pct" in equity_df.columns:
            max_dd = float(pd.to_numeric(equity_df["drawdown_pct"], errors="coerce").max())
        equity_col_used, eq_series = _equity_values(equity_df)
        if eq_series is not None and not eq_series.empty:
            if max_dd is None:
                max_dd = _max_drawdown_from_equity(eq_series)
            final_equity = float(eq_series.iloc[-1])

    hook_summary: Dict[str, Any] = {}
    if hook_log is not None:
        hook_summary = _summarize_hook_log(hook_log, days)

    return {
        "phase": phase,
        "duration_days": days,
        "total_trades": total_trades,
        "win_trades": win_trades,
        "win_rate": round(win_rate, 6),
        "gross_profit_usd": round(gross_profit, 4),
        "gross_loss_usd": round(gross_loss, 4),
        "profit_factor": None if profit_factor is None else round(float(profit_factor), 6),
        "max_drawdown_pct": None if max_dd is None else round(float(max_dd), 6),
        "final_equity": None if final_equity is None else round(float(final_equity), 6),
        "equity_column": equity_col_used,
        "source_paths": {
            "trades": str(trades_path) if trades_path else None,
            "equity": str(equity_path) if equity_path else None,
        },
        "hybrid_scoring_hook": hook_summary,
        "exported_at": datetime.now(timezone.utc).isoformat(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Export control/hybrid phase metrics to JSON.")
    parser.add_argument("--phase", required=True, choices=["control", "hybrid"])
    parser.add_argument("--days", type=int, default=3)
    parser.add_argument("--output", required=True, help="Output JSON path (e.g. reports/phaseA_metrics.json)")
    parser.add_argument(
        "--trades-path",
        default="",
        help="Trades JSONL (default: logs/paper_trades.jsonl or data/paper_trades_fallback.jsonl)",
    )
    parser.add_argument(
        "--equity-path",
        default="",
        help="Equity CSV (default: logs/equity_curve.csv if present)",
    )
    parser.add_argument(
        "--hook-log",
        default="",
        help="Hybrid hook JSONL for event counts (default: logs/hybrid_scoring_hook.jsonl)",
    )
    args = parser.parse_args()

    trades_arg = (ROOT / args.trades_path).resolve() if str(args.trades_path).strip() else None
    equity_arg = (ROOT / args.equity_path).resolve() if str(args.equity_path).strip() else None
    hook_arg = (ROOT / args.hook_log).resolve() if str(args.hook_log).strip() else None

    trades_path = trades_arg
    if trades_path is None or not trades_path.is_file():
        trades_path = _first_existing(
            [
                ROOT / "logs" / "paper_trades.jsonl",
                ROOT / "data" / "paper_trades_fallback.jsonl",
            ]
        )

    equity_path = equity_arg
    if equity_path is None or not equity_path.is_file():
        equity_path = _first_existing([ROOT / "logs" / "equity_curve.csv"])

    hook_log = hook_arg if hook_arg and hook_arg.is_file() else (ROOT / "logs" / "hybrid_scoring_hook.jsonl")

    metrics = compute_metrics(trades_path, equity_path, int(args.days), str(args.phase), hook_log)

    out_path = (ROOT / args.output).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2)

    print(f"OK metrics exported to {out_path}")
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
