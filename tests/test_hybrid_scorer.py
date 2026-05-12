"""Tests for models.hybrid_scorer.compute_hybrid_score."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.hybrid_scorer

from models.hybrid_scorer import compute_hybrid_score


def _cfg(**hybrid_keys):
    hs = {
        "enabled": True,
        "min_hybrid_threshold": 0.0,
        "weighting": {"ict": 0.6, "trend_alignment": 0.25, "nn_confidence": 0.15},
    }
    hs.update(hybrid_keys)
    return {"hybrid_scoring": hs}


def test_bullish_aligned_high_score():
    sig = {"direction": "bullish", "confluence_score": 80}
    r = compute_hybrid_score(sig, 0.5, _cfg(min_hybrid_threshold=0.0))
    assert r["hybrid_score"] > 0.4
    assert r["trend_alignment"] == "aligned"


def test_opposed_alignment():
    sig = {"direction": "bullish", "confluence_score": 50}
    r = compute_hybrid_score(sig, -0.8, _cfg(min_hybrid_threshold=0.0))
    assert r["trend_alignment"] == "opposed"


def test_threshold_gates_to_zero():
    sig = {"direction": "bullish", "confluence_score": 10}
    r = compute_hybrid_score(sig, 0.01, _cfg(min_hybrid_threshold=0.99))
    assert r["hybrid_score"] == 0.0


def test_missing_keys_defaults():
    sig = {}
    r = compute_hybrid_score(sig, 0.0, {})
    assert r["hybrid_score"] == 0.0
    assert "weights_applied" in r
