"""
Fuse ICT-style signal dict with trend score. Pure Python; no torch dependency.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Dict

logger = logging.getLogger(__name__)


def compute_hybrid_score(ict_signal: dict, trend_score: float, config: dict) -> dict:
    """
    Combine normalized ICT confluence with trend alignment and magnitude.

    Returns keys: hybrid_score, trend_alignment, weights_applied, nn_confidence (mirror of |trend|).
    """
    hs = (config or {}).get("hybrid_scoring") or {}
    w = hs.get("weighting") or {}
    w_ict = float(w.get("ict", 0.6))
    w_trend = float(w.get("trend_alignment", 0.25))
    w_nn = float(w.get("nn_confidence", 0.15))
    min_th = float(hs.get("min_hybrid_threshold", 0.65))

    if any(math.isnan(x) for x in (w_ict, w_trend, w_nn, min_th)) or math.isnan(float(trend_score)):
        logger.warning("hybrid_scorer: NaN weights or trend_score; returning zero score.")
        return {
            "hybrid_score": 0.0,
            "trend_alignment": "unknown",
            "weights_applied": {"ict": w_ict, "trend_alignment": w_trend, "nn_confidence": w_nn},
            "nn_confidence": 0.0,
        }

    s = float(trend_score)
    conf = abs(s)
    direction = str(ict_signal.get("direction", "") or "").lower()
    bullish_ict = direction in ("bullish", "long", "buy")
    bearish_ict = direction in ("bearish", "short", "sell")
    if s > 1e-9:
        trend_sign = 1
    elif s < -1e-9:
        trend_sign = -1
    else:
        trend_sign = 0

    if bullish_ict and trend_sign == 1:
        alignment = 1.0
        trend_alignment = "aligned"
    elif bearish_ict and trend_sign == -1:
        alignment = 1.0
        trend_alignment = "aligned"
    elif bullish_ict and trend_sign == -1:
        alignment = -1.0
        trend_alignment = "opposed"
    elif bearish_ict and trend_sign == 1:
        alignment = -1.0
        trend_alignment = "opposed"
    else:
        alignment = 0.0
        trend_alignment = "neutral"

    raw_conf = ict_signal.get("confluence_score", ict_signal.get("confluence", 0))
    try:
        conf_ict = float(raw_conf) / 100.0
    except (TypeError, ValueError):
        conf_ict = 0.0
    conf_ict = max(0.0, min(1.0, conf_ict))

    hybrid = w_ict * conf_ict + w_trend * alignment * conf + w_nn * conf
    hybrid = max(0.0, min(1.0, hybrid))
    if hybrid < min_th:
        hybrid = 0.0

    return {
        "hybrid_score": float(hybrid),
        "trend_alignment": trend_alignment,
        "weights_applied": {"ict": w_ict, "trend_alignment": w_trend, "nn_confidence": w_nn},
        "nn_confidence": float(conf),
    }
