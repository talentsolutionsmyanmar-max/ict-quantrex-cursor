#!/usr/bin/env python3
"""Run daily signal health check from local signal_audit DB."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from monitoring.signal_audit import daily_health_check


def main() -> int:
    out = daily_health_check(min_entries=1)
    print(json.dumps(out, indent=2))
    return 0 if out.get("pass") else 2


if __name__ == "__main__":
    raise SystemExit(main())
