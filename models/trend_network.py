"""
Lightweight CPU trend scorer (PyTorch MLP). YAML-gated at integration layer.

If PyTorch is missing or ``QUANTREX_DISABLE_TORCH=1``, returns neutral scores (zeros).
"""

from __future__ import annotations

import logging
import os
import warnings
from typing import Any, Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

TORCH_AVAILABLE = False
_torch: Any = None
_nn: Any = None

try:
    import torch
    from torch import nn as _nn

    _torch = torch
    _nn = _nn
    TORCH_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised when torch not installed
    torch = None  # type: ignore
    _nn = None


class TrendNetwork:
    """
    MLP: Linear -> ReLU -> ... -> Tanh on per-row feature vectors.

    ``predict`` returns one bounded score per DataFrame row (batch forward).
    """

    def __init__(
        self,
        input_dim: int,
        hidden_dims: Optional[Sequence[int]] = None,
        *,
        feature_columns: Optional[List[str]] = None,
    ) -> None:
        self.input_dim = int(input_dim)
        self.hidden_dims = list(hidden_dims or (32, 16))
        self.feature_columns = feature_columns or []
        self._torch_ok = TORCH_AVAILABLE and os.environ.get("QUANTREX_DISABLE_TORCH", "").strip() != "1"
        self._model: Any = None

        if self._torch_ok:
            assert _torch is not None and _nn is not None
            _torch.manual_seed(42)
            if _torch.cuda.is_available():
                _torch.cuda.manual_seed_all(42)
            layers: List[Any] = []
            prev = self.input_dim
            for h in self.hidden_dims:
                layers.append(_nn.Linear(prev, int(h)))
                layers.append(_nn.ReLU())
                prev = int(h)
            layers.append(_nn.Linear(prev, 1))
            layers.append(_nn.Tanh())
            self._model = _nn.Sequential(*layers)
            self._model.eval()
        else:
            if not TORCH_AVAILABLE:
                logger.warning("PyTorch unavailable; TrendNetwork uses neutral fallback.")
            else:
                logger.warning("QUANTREX_DISABLE_TORCH=1; TrendNetwork uses neutral fallback.")

    def _feature_matrix(self, df: pd.DataFrame) -> np.ndarray:
        cols = self.feature_columns
        if not cols:
            cols = [c for c in ("open", "high", "low", "close", "volume") if c in df.columns]
        if len(cols) != self.input_dim:
            # pad / trim to fixed width for stable linear layers
            if len(cols) < self.input_dim:
                extra = [c for c in df.columns if c not in cols and df[c].dtype in (np.float64, float, "float64", "float32")]
                for c in extra:
                    if len(cols) >= self.input_dim:
                        break
                    cols.append(c)
            cols = cols[: self.input_dim]
        X = df.reindex(columns=cols, fill_value=0.0).astype(np.float64).values
        if X.shape[1] < self.input_dim:
            X = np.pad(X, ((0, 0), (0, self.input_dim - X.shape[1])), mode="constant")
        elif X.shape[1] > self.input_dim:
            X = X[:, : self.input_dim]
        return X

    def predict(self, df: pd.DataFrame) -> np.ndarray:
        n = len(df)
        if n == 0:
            return np.array([], dtype=np.float64)
        if not self._torch_ok or self._model is None:
            return np.zeros(n, dtype=np.float64)
        X = self._feature_matrix(df)
        assert _torch is not None
        with _torch.no_grad():
            t = _torch.tensor(X, dtype=_torch.float32)
            out = self._model(t).numpy().reshape(-1)
        return np.clip(out.astype(np.float64), -1.0, 1.0)

    def feature_importance(self, input_tensor: Any) -> Dict[str, float]:
        """Simple gradient magnitude attribution w.r.t. input (per feature)."""
        if not self._torch_ok or self._model is None:
            return {}
        assert _torch is not None
        x = input_tensor.detach().clone().requires_grad_(True)
        self._model.zero_grad(set_to_none=True)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=UserWarning)
            y = self._model(x).sum()
        y.backward()
        if x.grad is None:
            return {}
        g = x.grad.abs().detach().cpu().numpy().reshape(-1)
        keys = self.feature_columns[: len(g)] if self.feature_columns else [f"f{i}" for i in range(len(g))]
        keys = keys + [f"f{i}" for i in range(len(keys), len(g))]
        return {keys[i]: float(g[i]) for i in range(min(len(g), len(keys)))}
