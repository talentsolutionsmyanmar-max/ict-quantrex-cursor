"""
Load strategy/spec.yaml and apply to a Config instance.
Maps nested YAML sections to Config attributes (institution-style single spec).
v2: STRATEGY_SPEC_PATH overrides file; nested ict.fvg / market.allocation / liquidity wired.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

_STRATEGY_DIR = Path(__file__).resolve().parent


def get_spec_path() -> Path:
    override = os.environ.get("STRATEGY_SPEC_PATH", "").strip()
    if override:
        p = Path(override)
        return p if p.is_absolute() else _STRATEGY_DIR / p
    return _STRATEGY_DIR / "spec.yaml"


# (section, yaml_key) -> Config attribute name
_MAP: Dict[Tuple[str, str], str] = {
    ("market", "symbol"): "SYMBOL",
    ("market", "timeframe"): "TIMEFRAME",
    ("market", "binance_spot_base_url"): "BINANCE_API",
    ("ict", "range_hours"): "ICT_RANGE_HOURS",
    ("ict", "liquidity_buffer"): "LIQUIDITY_BUFFER",
    ("ict", "fvg_threshold"): "FVG_THRESHOLD",
    ("ict", "ote_levels"): "OTE_LEVELS",
    ("risk", "initial_capital"): "INITIAL_CAPITAL",
    ("risk", "risk_per_trade"): "RISK_PER_TRADE",
    ("risk", "atr_multiplier"): "ATR_MULTIPLIER",
    ("risk", "min_confluence"): "MIN_CONFLUENCE",
    ("risk", "min_signal_strength"): "MIN_SIGNAL_STRENGTH",
    ("risk", "max_daily_loss"): "MAX_DAILY_LOSS",
    ("risk", "max_drawdown"): "MAX_DRAWDOWN",
    ("risk", "max_position_notional_usd"): "MAX_POSITION_NOTIONAL_USD",
    ("execution", "tp1_ratio"): "TP1_RATIO",
    ("execution", "tp2_ratio"): "TP2_RATIO",
    ("execution", "tp3_ratio"): "TP3_RATIO",
    ("execution", "tp1_pct"): "TP1_PCT",
    ("execution", "tp2_pct"): "TP2_PCT",
    ("execution", "tp3_pct"): "TP3_PCT",
    ("execution", "trail_after_tp1"): "TRAIL_AFTER_TP1",
    ("execution", "trail_atr_multiplier"): "TRAIL_ATR_MULTIPLIER",
    ("execution", "max_candles_hold"): "MAX_CANDLES_HOLD",
    ("execution", "model_partial_fills"): "MODEL_PARTIAL_FILLS",
    ("backtest", "start_date"): "BACKTEST_START_DATE",
    ("backtest", "end_date"): "BACKTEST_END_DATE",
    ("backtest", "commission"): "COMMISSION",
    ("backtest", "slippage"): "SLIPPAGE",
    ("operations", "poll_interval_sec"): "POLL_INTERVAL_SEC",
    ("operations", "mode"): "MODE",
    ("operations", "log_every_fill"): "LOG_EVERY_FILL",
    ("regime", "enabled"): "REGIME_GATE_ENABLED",
    ("regime", "adx_period"): "REGIME_ADX_PERIOD",
    ("regime", "adx_min"): "REGIME_ADX_MIN",
    ("regime", "atr_period"): "REGIME_ATR_PERIOD",
    ("regime", "atr_pct_min"): "REGIME_ATR_PCT_MIN",
    ("regime", "ema_fast"): "REGIME_EMA_FAST",
    ("regime", "ema_slow"): "REGIME_EMA_SLOW",
    ("regime", "persist_bars"): "REGIME_PERSIST_BARS",
    ("regime", "range_min_signal_strength"): "REGIME_RANGE_MIN_SIGNAL_STRENGTH",
    ("regime", "range_min_confluence"): "REGIME_RANGE_MIN_CONFLUENCE",
}


def read_raw_spec(path: Optional[Path] = None) -> Dict[str, Any]:
    p = path or get_spec_path()
    if not p.is_file():
        return {}
    with open(p, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data if isinstance(data, dict) else {}


def _apply_v2_nested(cfg: Any, raw: Dict[str, Any]) -> None:
    market = raw.get("market")
    if isinstance(market, dict):
        alloc = market.get("allocation")
        if isinstance(alloc, dict) and hasattr(cfg, "ALLOCATION_METHOD"):
            mth = alloc.get("method")
            if mth is not None:
                cfg.ALLOCATION_METHOD = str(mth)
            if "max_concurrent_positions" in alloc and hasattr(cfg, "MAX_CONCURRENT_POSITIONS"):
                v = alloc.get("max_concurrent_positions")
                cfg.MAX_CONCURRENT_POSITIONS = int(v) if v is not None else None
            if "correlation_cap" in alloc and hasattr(cfg, "CORRELATION_CAP"):
                cfg.CORRELATION_CAP = float(alloc.get("correlation_cap", 0.7))
            if "correlation_reduce_threshold" in alloc and hasattr(cfg, "CORRELATION_REDUCE_THRESHOLD"):
                cfg.CORRELATION_REDUCE_THRESHOLD = float(alloc.get("correlation_reduce_threshold", 0.85))

    ict = raw.get("ict")
    if isinstance(ict, dict):
        fvg = ict.get("fvg")
        if isinstance(fvg, dict):
            if hasattr(cfg, "FVG_METHOD") and fvg.get("method") is not None:
                cfg.FVG_METHOD = str(fvg.get("method", "static")).lower()
            if hasattr(cfg, "FVG_MIN_GAP_ATR") and fvg.get("min_gap_atr") is not None:
                cfg.FVG_MIN_GAP_ATR = float(fvg.get("min_gap_atr", 0.3))
            if hasattr(cfg, "FVG_CONFIRMATION_CANDLES") and fvg.get("confirmation_candles") is not None:
                cfg.FVG_CONFIRMATION_CANDLES = int(fvg.get("confirmation_candles", 0))
            if hasattr(cfg, "FVG_MITIGATION_FILTER") and fvg.get("mitigation_filter") is not None:
                cfg.FVG_MITIGATION_FILTER = bool(fvg.get("mitigation_filter"))
            if hasattr(cfg, "FVG_IGNORE_MITIGATED") and fvg.get("ignore_mitigated") is not None:
                cfg.FVG_IGNORE_MITIGATED = bool(fvg.get("ignore_mitigated"))

        liq = ict.get("liquidity")
        if isinstance(liq, dict) and hasattr(cfg, "SWEEP_VOLUME_SPIKE_FACTOR"):
            vsf = liq.get("volume_spike_factor")
            if vsf is not None:
                cfg.SWEEP_VOLUME_SPIKE_FACTOR = float(vsf)


def get_spec_meta(path: Optional[Path] = None) -> Dict[str, Any]:
    raw = read_raw_spec(path)
    meta = raw.get("meta") or {}
    return {
        "spec_version": raw.get("spec_version"),
        "name": meta.get("name"),
        "description": meta.get("description"),
    }


def get_kill_zones(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    raw = read_raw_spec(path)
    sessions = raw.get("sessions") or {}
    zones = sessions.get("kill_zones")
    return list(zones) if isinstance(zones, list) else []


def get_gates(path: Optional[Path] = None) -> Dict[str, Any]:
    raw = read_raw_spec(path)
    g = raw.get("gates") or {}
    return g if isinstance(g, dict) else {}


def apply_spec_to_config(cfg: Any, path: Optional[Path] = None) -> None:
    raw = read_raw_spec(path)
    if not raw:
        return
    for section, fields in raw.items():
        if section in ("spec_version", "meta", "sessions", "gates"):
            continue
        if not isinstance(fields, dict):
            continue
        for key, value in fields.items():
            attr = _MAP.get((section, key))
            if not attr or not hasattr(cfg, attr):
                continue
            setattr(cfg, attr, value)

    market = raw.get("market")
    if isinstance(market, dict):
        wl = market.get("watchlist")
        if isinstance(wl, list) and hasattr(cfg, "WATCHLIST"):
            clean = [str(x).upper().replace("/", "") for x in wl if x]
            cfg.WATCHLIST = clean if clean else None

    _apply_v2_nested(cfg, raw)


_PUBLIC_KEYS = (
    "spec_version",
    "meta",
    "market",
    "ict",
    "risk",
    "execution",
    "backtest",
    "operations",
    "regime",
    "sessions",
    "gates",
    "walk_forward",
    "dirty_execution",
    "stress_tests",
    "observability",
    "evolution",
    "validation",
)


def public_spec_dict(path: Optional[Path] = None) -> Dict[str, Any]:
    """Safe for API: no secrets."""
    raw = read_raw_spec(path)
    out: Dict[str, Any] = {}
    for k in _PUBLIC_KEYS:
        if k in raw and raw[k] is not None:
            out[k] = raw[k]
    return out
