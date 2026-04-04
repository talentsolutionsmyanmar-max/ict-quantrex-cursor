"""
Evolve on a shorter window (fast), then verify rank #1 on the full period.

First run fills data/klines_cache/; repeat runs are much faster.

Usage:
  ./venv/bin/python scripts/run_evolution_and_verify_v18.py

Env:
  DISABLE_KLINES_CACHE=1  — force live Binance fetch (no pickle cache)
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backtester import Backtester
from config import build_config
from research_lab import apply_research_genes, run_evolution


def _default_symbols() -> list[str]:
    cfg = build_config()
    wl = list(getattr(cfg, "WATCHLIST", []) or [])
    syms = [str(s).upper().replace("/", "") for s in wl if s]
    # Broader robustness basket (fallback if watchlist is missing/too small).
    if len(syms) < 6:
        syms = ["SOLUSDT", "ETHUSDT", "BTCUSDT", "BNBUSDT", "XRPUSDT", "ADAUSDT"]
    return syms[:8]


def main() -> None:
    symbols = _default_symbols()
    timeframe = "1h"
    initial_capital = 10000

    # Shorter window for search (my call: ~2y train/test split still robust, much less wall time)
    evolve_start = "2024-01-01"
    evolve_end = "2025-12-31"

    # Full audit on winner only
    verify_start = "2024-01-01"
    verify_end = "2026-03-30"

    print(
        f"[pipeline] evolve {evolve_start}..{evolve_end} | verify winner {verify_start}..{verify_end} | symbols={symbols}",
        flush=True,
    )

    base = build_config()
    evo = run_evolution(
        symbol="BTCUSDT",
        symbols=symbols,
        timeframe=timeframe,
        start_date=evolve_start,
        end_date=evolve_end,
        initial_capital=initial_capital,
        population=12,
        generations=6,
        verify_top_k_crisis=1,
        seed=42,
        runtime_cfg=base,
    )

    if not evo.get("top"):
        raise RuntimeError(f"No top results returned: keys={list(evo.keys())}")

    rank1_genes = evo["top"][0]["genes"]
    print("RANK1_GENES_START")
    print(json.dumps(rank1_genes, sort_keys=True))
    print("RANK1_GENES_END")

    candidate = build_config()
    errs = apply_research_genes(candidate, rank1_genes)
    if errs:
        raise RuntimeError(f"apply_research_genes errors: {errs}")
    candidate.RISK_PER_TRADE = 0.01
    candidate.TIMEFRAME = timeframe

    print("[pipeline] full multi-coin verification on rank #1 …", flush=True)
    results = Backtester.run_multi(
        base_config=candidate,
        symbols=symbols,
        timeframe=timeframe,
        start_date=verify_start,
        end_date=verify_end,
        initial_capital=initial_capital,
        max_workers=1,
        verbose=False,
    )
    print("AGGREGATE_START")
    print(json.dumps(results.get("aggregate"), sort_keys=True))
    print("AGGREGATE_END")


if __name__ == "__main__":
    main()
