#!/usr/bin/env python3
import logging
import sys
from pathlib import Path
from typing import List

import yaml

logger = logging.getLogger("quantrex.config")
if not logger.handlers:
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(logging.Formatter("%(levelname)s | %(message)s"))
    logger.addHandler(h)
logger.setLevel(logging.INFO)


def load_and_verify_config(config_path: str, expected_keys: List[str]):
    """Loads YAML and asserts critical keys are present. Fails fast if missing."""
    p = Path(config_path)
    if not p.exists():
        logger.critical(f"Config not found: {config_path}")
        sys.exit(1)

    with p.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    for key in expected_keys:
        if not cfg.get(key):
            logger.critical(f"Missing critical config key: {key}")
            sys.exit(1)

    logger.info(f"CONFIG LOADED: {config_path}")
    logger.info(f"exits.stop_loss_r = {cfg.get('exits', {}).get('stop_loss_r')}")
    logger.info(
        "exits.trend_up.trail_start_r = "
        f"{cfg.get('exits', {}).get('trend_up', {}).get('trail_start_r')}"
    )
    logger.info(
        "trading_universe.allowed_regimes = "
        f"{cfg.get('trading_universe', {}).get('allowed_regimes')}"
    )
    return cfg


if __name__ == "__main__":
    load_and_verify_config(
        "config/regime_isolation_v2.3.yaml",
        expected_keys=["trading_universe", "exits", "risk"],
    )
