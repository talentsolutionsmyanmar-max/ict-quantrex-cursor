#!/usr/bin/env python3
"""
v2.8.1 — Read-only multi-asset DOM JSONL growth snapshot (Golden Lock safe).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FILES = {
    "BTC": ROOT / "data" / "dom_btc_audit.jsonl",
    "ETH": ROOT / "data" / "dom_eth_audit.jsonl",
    "SOL": ROOT / "data" / "dom_sol_audit.jsonl",
}
PREFLIGHT = ROOT / "reports" / "dom_cvd_day7_preflight.json"


def _count_and_24h(path: Path) -> tuple[int, int]:
    total = 0
    last24 = 0
    if not path.exists():
        return 0, 0
    cut = datetime.now(timezone.utc) - timedelta(hours=24)
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        total += 1
        try:
            ts = json.loads(line).get("ts")
            if not ts:
                continue
            t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            if t >= cut:
                last24 += 1
        except (json.JSONDecodeError, ValueError, TypeError):
            pass
    return total, last24


def main():
    print("MULTI-ASSET DOM GROWTH CHECK (read-only)")
    print("=" * 50)
    for label, path in FILES.items():
        n, n24 = _count_and_24h(path)
        print(f"  {label:3s} | total_lines={n:5d} | last_24h={n24:3d} | {path.name}")
    print("=" * 50)
    print("  Target (steady 15m cadence): ~96 rows/asset per 24h")

    if PREFLIGHT.exists():
        pre = json.loads(PREFLIGHT.read_text(encoding="utf-8"))
        print(f"  ready_for_day7 (last preflight): {pre.get('ready_for_day7', 'n/a')}")
    else:
        print("  ready_for_day7: (no preflight file yet — run dom_cvd_day7_preflight.py)")

    print("=" * 50)


if __name__ == "__main__":
    main()
