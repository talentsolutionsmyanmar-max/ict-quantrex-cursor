#!/usr/bin/env python3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.config_loader import load_and_verify_config


if __name__ == "__main__":
    load_and_verify_config(
        "config/regime_isolation_v2.3.yaml",
        expected_keys=["trading_universe", "exits", "risk"],
    )
