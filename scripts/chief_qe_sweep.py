#!/usr/bin/env python3
"""
Chief QE — stability sweep + cost stress (multi-coin). For VPS / CI / local.

Usage (from repo root, venv active):
  python scripts/chief_qe_sweep.py
  GENES_JSON='{"MIN_SIGNAL_STRENGTH":68,...}' python scripts/chief_qe_sweep.py

Env:
  START_DATE, END_DATE, TIMEFRAME, N_WINDOWS, FRICTION_MULT, SYMBOLS (comma-separated)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import build_config
from quant_rigor import build_runner_from_lab, run_cost_stress, run_stability_sweep


def _symbols() -> list[str]:
    raw = os.getenv("SYMBOLS", "").strip()
    if raw:
        return [x.strip().upper().replace("/", "") for x in raw.split(",") if x.strip()]
    cfg = build_config()
    wl = list(getattr(cfg, "WATCHLIST", []) or [])
    syms = [str(s).upper().replace("/", "") for s in wl if s]
    if len(syms) < 3:
        syms = ["SOLUSDT", "ETHUSDT", "BTCUSDT"]
    return syms[:8]


def main() -> None:
    base = build_config()
    gj = os.getenv("GENES_JSON", "").strip()
    genes = json.loads(gj) if gj else None

    start = os.getenv("START_DATE", base.BACKTEST_START_DATE)
    end = os.getenv("END_DATE", base.BACKTEST_END_DATE)
    tf = os.getenv("TIMEFRAME", base.TIMEFRAME)
    n_win = int(os.getenv("N_WINDOWS", "3"))
    friction = float(os.getenv("FRICTION_MULT", "1.5"))
    syms = _symbols()

    try:
        runner = build_runner_from_lab(runtime_cfg=base, genes=genes if isinstance(genes, dict) else None)
    except ValueError as e:
        raise SystemExit(str(e)) from e

    print(f"[chief_qe] symbols={syms} tf={tf} range={start}..{end}", flush=True)
    stab = run_stability_sweep(
        runner,
        symbols=syms,
        timeframe=tf,
        start_date=start,
        end_date=end,
        n_windows=n_win,
        max_workers=1,
    )
    print("STABILITY_START", flush=True)
    print(json.dumps(stab, default=str, indent=2), flush=True)
    print("STABILITY_END", flush=True)

    cost = run_cost_stress(
        runner,
        symbols=syms,
        timeframe=tf,
        start_date=start,
        end_date=end,
        friction_mult=friction,
        max_workers=1,
    )
    print("COST_STRESS_START", flush=True)
    print(json.dumps(cost, default=str, indent=2), flush=True)
    print("COST_STRESS_END", flush=True)

    s_ok = stab.get("verdict") != "FAIL"
    c_ok = cost.get("success") and cost.get("verdict") != "FAIL"
    raise SystemExit(0 if (s_ok and c_ok) else 1)


if __name__ == "__main__":
    main()
