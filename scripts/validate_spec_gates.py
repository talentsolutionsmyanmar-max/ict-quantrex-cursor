#!/usr/bin/env python3
"""
YAML gate validator — enforces arithmetic and logical rules in strategy/spec.yaml.

Run before backtests / paper execution. Fails fast on misconfigured hybrid weights
or contradictory gates. Complements string-only validation.business_rules (not evaluated in-repo).
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import yaml

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SPEC = ROOT / "strategy" / "spec.yaml"


def spec_path() -> Path:
    override = os.environ.get("STRATEGY_SPEC_PATH", "").strip()
    if override:
        p = Path(override)
        return p if p.is_absolute() else ROOT / p
    return DEFAULT_SPEC


def load_spec() -> dict:
    path = spec_path()
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data if isinstance(data, dict) else {}


def validate_weights(spec: dict) -> bool:
    if not spec.get("hybrid_scoring", {}).get("enabled", False):
        logger.info("hybrid_scoring disabled; skipping weight sum validation.")
        return True
    weights = spec["hybrid_scoring"].get("weighting", {})
    required = ["ict", "trend_alignment", "nn_confidence"]
    if not all(k in weights for k in required):
        logger.error("Missing required weighting keys: %s", required)
        return False
    total = sum(float(weights[k]) for k in required)
    if abs(total - 1.0) > 1e-6:
        logger.error("Weighting sum = %.6f (must be 1.0 +/- 1e-6)", total)
        return False
    logger.info("Weighting sum = %.6f (PASS)", total)
    return True


def validate_gates(spec: dict) -> bool:
    phase0 = bool(spec.get("adaptation_gates", {}).get("phase_0_only", True))
    hybrid_enabled = bool(spec.get("hybrid_scoring", {}).get("enabled", False))
    if hybrid_enabled and phase0:
        logger.error(
            "VIOLATION: hybrid_scoring.enabled=true while adaptation_gates.phase_0_only=true"
        )
        return False
    logger.info(
        "Gate logic consistent (phase_0_only=%s, hybrid_scoring.enabled=%s)",
        phase0,
        hybrid_enabled,
    )
    return True


def validate_thresholds(spec: dict) -> bool:
    threshold = float(spec.get("hybrid_scoring", {}).get("min_hybrid_threshold", 0.65))
    if not (0.0 <= threshold <= 1.0):
        logger.error("min_hybrid_threshold=%s out of bounds [0, 1]", threshold)
        return False
    logger.info("min_hybrid_threshold=%s (PASS)", threshold)
    return True


def main() -> int:
    path = spec_path()
    logger.info("Loading spec from %s", path)
    try:
        spec = load_spec()
    except Exception as e:
        logger.error("Failed to load spec: %s", e)
        return 1

    checks = [validate_weights, validate_gates, validate_thresholds]
    passed = all(c(spec) for c in checks)
    if passed:
        logger.info("All gate validations PASSED.")
        return 0
    logger.error("Gate validation FAILED. Fix YAML before proceeding.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
