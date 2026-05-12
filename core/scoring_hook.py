"""
Gated hybrid scoring hook: optional trend network + fusion into signal metadata.

Zero regression path: disabled flags, missing torch, or any error => original signals
returned as deep copies without mutation.
"""

from __future__ import annotations

import copy
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]


class HybridScoringHook:
    def __init__(self, config: Dict[str, Any]) -> None:
        self.config = config or {}

    def _append_obs_log(self, row: Dict[str, Any]) -> None:
        obs = self.config.get("observability") or {}
        if not obs.get("log_signals", True):
            return
        log_dir = ROOT / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / "hybrid_scoring_hook.jsonl"
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, default=str) + "\n")

    def apply(self, signals: List[dict], df: pd.DataFrame) -> List[dict]:
        t_apply_start = time.perf_counter()
        out = [copy.deepcopy(s) for s in signals]
        hs = self.config.get("hybrid_scoring") or {}
        if not hs.get("enabled", False):
            elapsed_ms = (time.perf_counter() - t_apply_start) * 1000.0
            logger.info("HybridScoringHook: hybrid_scoring disabled; passthrough.")
            self._append_obs_log(
                {
                    "kind": "hybrid_scoring_hook",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "event": "scoring_hook_passthrough",
                    "reason": "hybrid_scoring_disabled",
                    "signal_count": len(out),
                    "latency_ms": round(elapsed_ms, 4),
                }
            )
            return out

        from models.trend_network import TORCH_AVAILABLE, TrendNetwork

        if not TORCH_AVAILABLE:
            elapsed_ms = (time.perf_counter() - t_apply_start) * 1000.0
            logger.warning("HybridScoringHook: PyTorch unavailable; passthrough.")
            self._append_obs_log(
                {
                    "kind": "hybrid_scoring_hook",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "event": "scoring_hook_passthrough",
                    "reason": "torch_unavailable",
                    "signal_count": len(out),
                    "latency_ms": round(elapsed_ms, 4),
                }
            )
            return out

        try:
            tn_cfg = hs.get("trend_network") or {}
            feats = list(tn_cfg.get("input_features") or ["open", "high", "low", "close", "volume"])
            feats = [f for f in feats if f in df.columns]
            if not feats:
                feats = [c for c in ("open", "high", "low", "close", "volume") if c in df.columns]
            input_dim = int(tn_cfg.get("input_dim", max(5, len(feats))))
            hidden = tuple(tn_cfg.get("hidden_layers") or (32, 16))
            net = TrendNetwork(input_dim, hidden_dims=hidden, feature_columns=feats)
            t_inf = time.perf_counter()
            scores = net.predict(df)
            inference_ms = (time.perf_counter() - t_inf) * 1000.0
            trend_score = float(scores[-1]) if len(scores) else 0.0

            from models.hybrid_scorer import compute_hybrid_score

            for sig in out:
                meta = sig.setdefault("metadata", {})
                fused = compute_hybrid_score(sig, trend_score, self.config)
                meta["hybrid_score"] = fused["hybrid_score"]
                meta["trend_alignment"] = fused["trend_alignment"]
                meta["nn_confidence"] = fused["nn_confidence"]
                meta["trend_raw"] = trend_score
                meta["inference_ms"] = round(inference_ms, 3)

            self._append_obs_log(
                {
                    "kind": "hybrid_scoring_hook",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "event": "scoring_hook_active",
                    "reason": "hybrid_scoring_enabled",
                    "signal_count": len(out),
                    "inference_ms": round(inference_ms, 3),
                    "trend_last": trend_score,
                }
            )
            return out
        except Exception as e:
            logger.exception("HybridScoringHook: inference failed; passthrough. err=%s", e)
            elapsed_ms = (time.perf_counter() - t_apply_start) * 1000.0
            self._append_obs_log(
                {
                    "kind": "hybrid_scoring_hook",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "event": "scoring_hook_passthrough",
                    "reason": "inference_error",
                    "signal_count": len(signals),
                    "latency_ms": round(elapsed_ms, 4),
                    "error": str(e),
                }
            )
            return [copy.deepcopy(s) for s in signals]
