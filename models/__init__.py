"""Phase 1 optional models (YAML-gated at call sites)."""

from .trend_network import TORCH_AVAILABLE, TrendNetwork

__all__ = ["TrendNetwork", "TORCH_AVAILABLE"]
