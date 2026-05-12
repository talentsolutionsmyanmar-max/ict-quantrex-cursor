#!/usr/bin/env python3
"""One-shot check for free Binance OPEN enrichment (no API keys). Run from repo root."""

from __future__ import annotations

import argparse
import json
import os
import sys

# Allow `python scripts/probe_binance_public_enrich.py` from repo root
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from binance_public_enrich import binance_free_entry_enrichment  # noqa: E402
from config import build_config  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description="Probe binance_public_entry bundle (spot aggTrades + USDM premium + OI hist).")
    p.add_argument("--symbol", default="BTCUSDT", help="Venue symbol, e.g. BTCUSDT")
    p.add_argument("--timeout", type=float, default=5.0)
    p.add_argument("--agg-limit", type=int, default=400)
    args = p.parse_args()

    cfg = build_config()
    spot = str(getattr(cfg, "BINANCE_API", "") or "https://api.binance.com/api/v3")
    fut = str(getattr(cfg, "BINANCE_FUTURES_API", "") or "https://fapi.binance.com/fapi/v1")

    out = binance_free_entry_enrichment(
        symbol=args.symbol,
        spot_base_url=spot,
        futures_api_v1_url=fut,
        agg_trades_limit=int(args.agg_limit),
        timeout_sec=float(args.timeout),
    )
    print(json.dumps(out, indent=2, default=str))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
