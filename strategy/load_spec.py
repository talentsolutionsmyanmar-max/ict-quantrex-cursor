"""
Load strategy/spec.yaml and apply to a Config instance.
Maps nested YAML sections to Config attributes (institution-style single spec).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

_SPEC_PATH = Path(__file__).resolve().parent / "spec.yaml"

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
    ("backtest", "start_date"): "BACKTEST_START_DATE",
    ("backtest", "end_date"): "BACKTEST_END_DATE",
    ("backtest", "commission"): "COMMISSION",
    ("backtest", "slippage"): "SLIPPAGE",
    ("operations", "poll_interval_sec"): "POLL_INTERVAL_SEC",
    ("operations", "mode"): "MODE",
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
    p = path or _SPEC_PATH
    if not p.is_file():
        return {}
    with open(p, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data if isinstance(data, dict) else {}


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


def public_spec_dict(path: Optional[Path] = None) -> Dict[str, Any]:
    """Safe for API: no secrets."""
    raw = read_raw_spec(path)
    out = {
        "spec_version": raw.get("spec_version"),
        "meta": raw.get("meta"),
        "market": raw.get("market"),
        "ict": raw.get("ict"),
        "risk": raw.get("risk"),
        "execution": raw.get("execution"),
        "backtest": raw.get("backtest"),
        "operations": raw.get("operations"),
        "regime": raw.get("regime"),
        "sessions": raw.get("sessions"),
        "gates": raw.get("gates"),
    }
    return {k: v for k, v in out.items() if v is not None}
