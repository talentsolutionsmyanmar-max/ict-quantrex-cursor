"""Tests for core.scoring_hook.HybridScoringHook."""

from __future__ import annotations

import copy

import pandas as pd
import pytest

pytestmark = pytest.mark.scoring_hook

from core.scoring_hook import HybridScoringHook


def _df():
    return pd.DataFrame(
        {
            "open": [100.0, 101.0],
            "high": [102.0, 102.5],
            "low": [99.0, 100.0],
            "close": [101.0, 101.5],
            "volume": [1000.0, 1100.0],
        }
    )


def test_disabled_passthrough():
    cfg = {"hybrid_scoring": {"enabled": False}, "observability": {"log_signals": False}}
    hook = HybridScoringHook(cfg)
    sigs = [{"id": 1, "direction": "bullish", "confluence_score": 70}]
    orig = copy.deepcopy(sigs)
    out = hook.apply(sigs, _df())
    assert out == orig
    assert "metadata" not in out[0]


def test_enabled_with_mock_network(monkeypatch):
    cfg = {
        "hybrid_scoring": {
            "enabled": True,
            "min_hybrid_threshold": 0.0,
            "weighting": {"ict": 0.6, "trend_alignment": 0.25, "nn_confidence": 0.15},
            "trend_network": {"input_dim": 5, "hidden_layers": [8, 4], "input_features": ["open", "high", "low", "close", "volume"]},
        },
        "observability": {"log_signals": False},
    }

    class FakeNet:
        def predict(self, df):
            return __import__("numpy").ones(len(df)) * 0.5

    monkeypatch.setattr("models.trend_network.TORCH_AVAILABLE", True)
    monkeypatch.setattr("models.trend_network.TrendNetwork", lambda *a, **k: FakeNet())

    hook = HybridScoringHook(cfg)
    sigs = [{"direction": "bullish", "confluence_score": 80}]
    out = hook.apply(sigs, _df())
    assert "metadata" in out[0]
    assert "hybrid_score" in out[0]["metadata"]


def test_network_failure_passthrough(monkeypatch):
    cfg = {
        "hybrid_scoring": {
            "enabled": True,
            "min_hybrid_threshold": 0.0,
            "trend_network": {"input_dim": 5},
        },
        "observability": {"log_signals": False},
    }

    class Boom:
        def predict(self, df):
            raise RuntimeError("simulated failure")

    monkeypatch.setattr("models.trend_network.TORCH_AVAILABLE", True)
    monkeypatch.setattr("models.trend_network.TrendNetwork", lambda *a, **k: Boom())

    hook = HybridScoringHook(cfg)
    sigs = [{"a": 1}]
    out = hook.apply(sigs, _df())
    assert out[0] == sigs[0]
    assert "hybrid_score" not in out[0].get("metadata", {})
