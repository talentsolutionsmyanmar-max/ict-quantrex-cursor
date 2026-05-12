#!/usr/bin/env python3
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path


def generate_audit_histogram(audit_path: str = "data/signal_audit.jsonl") -> None:
    p = Path(audit_path)
    if not p.exists():
        print("No audit data yet.")
        return

    rows = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except Exception:
            continue

    skips = [r.get("skip_reason", "unknown") for r in rows if str(r.get("decision", "")).upper() == "SKIP"]
    counts = Counter(skips)
    print("\nSKIP REASON HISTOGRAM (Last Audit Run):")
    for reason, count in counts.most_common():
        print(f"   {str(reason):20s} | {int(count):>4} skips")
    print(f"   {'TOTAL':20s} | {len(skips):>4}\n")

    regime_counts = Counter(str(r.get("regime", "unknown")) for r in rows)
    print("REGIME DISTRIBUTION:")
    for reg, cnt in regime_counts.most_common():
        print(f"   {reg:20s} | {int(cnt):>4} candles")


if __name__ == "__main__":
    generate_audit_histogram()
