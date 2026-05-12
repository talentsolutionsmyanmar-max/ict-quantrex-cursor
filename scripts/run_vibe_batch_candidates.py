#!/usr/bin/env python3
"""Run multiple Vibe candidates and write normalized JSON files for comparator."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


PROMPT_STYLES = [
    "ICT + FVG + sweep confirmation with strict risk controls",
    "SMC-inspired trend continuation with pullback confirmation",
    "Regime-aware crypto strategy using volatility and momentum alignment",
    "Mean-reversion around premium/discount zones with stop discipline",
    "Hybrid breakout + liquidity sweep filter for 15m crypto bars",
]


def _extract_json_objects(text: str) -> List[Dict[str, Any]]:
    objs: List[Dict[str, Any]] = []
    stack = 0
    start = -1
    in_str = False
    esc = False
    for i, ch in enumerate(text):
        if esc:
            esc = False
            continue
        if ch == "\\" and in_str:
            esc = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "{":
            if stack == 0:
                start = i
            stack += 1
        elif ch == "}":
            stack -= 1
            if stack == 0 and start >= 0:
                raw = text[start : i + 1]
                try:
                    obj = json.loads(raw)
                    if isinstance(obj, dict):
                        objs.append(obj)
                except Exception:
                    pass
    return objs


def _pick_backtest_metrics(objs: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for o in objs:
        if "profit_factor" in o and ("trade_count" in o or "max_drawdown" in o):
            return o
    return None


def _to_normalized(candidate_idx: int, bt: Dict[str, Any], *, start: str, end: str, symbol: str, timeframe: str, prompt: str) -> Dict[str, Any]:
    trade_count = float(bt.get("trade_count") or 0.0)
    total_return = float(bt.get("total_return") or 0.0)
    expectancy = (total_return * 100.0 / trade_count) if trade_count > 0 else 0.0
    dd_raw = float(bt.get("max_drawdown") or 0.0)
    dd_pct = abs(dd_raw) * 100.0 if abs(dd_raw) <= 1.0 else abs(dd_raw)
    win_rate_raw = float(bt.get("win_rate") or 0.0)
    win_rate_pct = win_rate_raw * 100.0 if win_rate_raw <= 1.0 else win_rate_raw

    return {
        "track": f"vibe_candidate_{candidate_idx:03d}",
        "source": "vibe_trading_backtest",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "window": {"start": start, "end": end},
        "request": {
            "symbol_scope": [symbol],
            "timeframe": timeframe,
            "notes": "Auto-generated via scripts/run_vibe_batch_candidates.py",
        },
        "metrics": {
            "profit_factor": float(bt.get("profit_factor") or 0.0),
            "max_drawdown": round(dd_pct, 4),
            "expectancy": round(expectancy, 4),
            "total_trades": trade_count,
            "unique_entries": trade_count,
            "win_rate": round(win_rate_pct, 4),
            "total_pnl": round(total_return * 100.0, 4),
            "sharpe_ratio": float(bt.get("sharpe") or 0.0),
        },
        "meta": {
            "candidate_prompt": prompt,
            "engine_version": "vibe-trading-ai 0.1.5",
            "normalization_notes": "unique_entries approximated by trade_count from Vibe runner output.",
        },
    }


def _run_one(prompt: str) -> str:
    cmd = ["python", "-m", "cli", "run", "-p", prompt, "--json"]
    cp = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore", check=False)
    return (cp.stdout or "") + "\n" + (cp.stderr or "")


def main() -> int:
    ap = argparse.ArgumentParser(description="Run Vibe batch candidates and export normalized JSON files.")
    ap.add_argument("--count", type=int, default=3, help="Number of candidates to run (max 5)")
    ap.add_argument("--start-date", required=True)
    ap.add_argument("--end-date", required=True)
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--timeframe", default="15m")
    ap.add_argument("--out-dir", default="reports")
    args = ap.parse_args()

    n = max(1, min(int(args.count), len(PROMPT_STYLES)))
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    successes = 0
    for i in range(1, n + 1):
        style = PROMPT_STYLES[i - 1]
        prompt = (
            f"Create one crypto strategy candidate ({style}). "
            f"Run backtest summary for {args.symbol} {args.timeframe} from {args.start_date} to {args.end_date}. "
            "Return full metrics."
        )
        raw = _run_one(prompt)
        objs = _extract_json_objects(raw)
        bt = _pick_backtest_metrics(objs)
        if bt is None:
            print(f"[candidate {i}] failed to parse Vibe backtest metrics.")
            continue
        normalized = _to_normalized(
            i,
            bt,
            start=args.start_date,
            end=args.end_date,
            symbol=args.symbol,
            timeframe=args.timeframe,
            prompt=prompt,
        )
        out_file = out_dir / f"vibe_candidate_{i:03d}.json"
        out_file.write_text(json.dumps(normalized, indent=2), encoding="utf-8")
        successes += 1
        print(f"[candidate {i}] wrote {out_file}")

    print(f"Completed: {successes}/{n} candidates exported.")
    return 0 if successes > 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
