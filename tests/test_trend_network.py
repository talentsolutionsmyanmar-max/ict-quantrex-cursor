"""Tests for models.trend_network.TrendNetwork."""

from __future__ import annotations

import os

import numpy as np
import pandas as pd
import pytest

pytestmark = pytest.mark.trend_network

from models.trend_network import TORCH_AVAILABLE, TrendNetwork


def _df(n: int = 20) -> pd.DataFrame:
    rng = np.random.default_rng(0)
    return pd.DataFrame(
        {
            "open": rng.uniform(99, 101, n),
            "high": rng.uniform(101, 103, n),
            "low": rng.uniform(97, 99, n),
            "close": rng.uniform(99, 101, n),
            "volume": rng.uniform(1e3, 2e3, n),
        }
    )


@pytest.mark.skipif(not TORCH_AVAILABLE, reason="torch not installed")
def test_tn_output_bounds_and_shape():
    net = TrendNetwork(5, hidden_dims=(32, 16), feature_columns=["open", "high", "low", "close", "volume"])
    df = _df(12)
    out = net.predict(df)
    assert out.shape == (12,)
    assert (out >= -1.0).all() and (out <= 1.0).all()


def test_neutral_fallback_when_torch_disabled(monkeypatch):
    monkeypatch.setenv("QUANTREX_DISABLE_TORCH", "1")
    net = TrendNetwork(5, feature_columns=["open", "high", "low", "close", "volume"])
    df = _df(8)
    out = net.predict(df)
    assert np.allclose(out, 0.0)
    monkeypatch.delenv("QUANTREX_DISABLE_TORCH", raising=False)


@pytest.mark.skipif(not TORCH_AVAILABLE, reason="torch not installed")
def test_feature_importance_keys():
    import torch

    net = TrendNetwork(3, hidden_dims=(8, 4), feature_columns=["open", "high", "close"])
    x = torch.zeros(1, 3, requires_grad=True)
    imp = net.feature_importance(x)
    assert isinstance(imp, dict)
    assert len(imp) <= 3
