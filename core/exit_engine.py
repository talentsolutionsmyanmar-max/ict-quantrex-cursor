from typing import Any, Dict, Optional


def calculate_r_multiple(entry_price: float, exit_price: float, stop_distance: float, direction: str = "long") -> float:
    """Use actual stop distance ratio for R, not account risk percent."""
    pnl_usd = (float(exit_price) - float(entry_price)) * (1.0 if str(direction).lower() == "long" else -1.0)
    stop_usd = abs(float(stop_distance)) * float(entry_price)
    return float(pnl_usd / stop_usd) if stop_usd > 0 else 0.0


def apply_regime_exit_logic(
    trade: Dict[str, Any],
    config_exits: Dict[str, Any],
    regime: str,
    *,
    regime_exits_merge: Optional[Dict[str, Any]] = None,
) -> str:
    """YAML-first regime logic; prevent early trend take-profit clipping.

    v2.5.1: Trail tightens only after unrealized_r >= trail_start_r (not immediately after breakeven).
    """
    regime_cfg = {}
    if isinstance(config_exits, dict):
        regime_cfg = {**(config_exits.get(regime, config_exits.get("default", {})) or {})}
    if regime_exits_merge:
        regime_cfg = {**regime_cfg, **regime_exits_merge}

    unrealized_r = float(trade.get("unrealized_r", 0.0) or 0.0)

    # BREAKEVEN ENFORCEMENT
    breakeven_cfg = regime_cfg.get("breakeven_at_r")
    if breakeven_cfg is not None and unrealized_r >= float(breakeven_cfg):
        buffer = float(regime_cfg.get("breakeven_move_to_r", 0.05))
        stop_dist = float(trade.get("stop_distance", 0.0) or 0.0)
        entry_px = float(trade.get("entry_price", 0.0) or 0.0)
        direction = str(trade.get("direction", "long")).lower()
        if direction == "long":
            be_stop = entry_px + (buffer * stop_dist)
            if be_stop > float(trade.get("stop_price", be_stop)):
                trade["stop_price"] = be_stop
        else:
            be_stop = entry_px - (buffer * stop_dist)
            if be_stop < float(trade.get("stop_price", be_stop)):
                trade["stop_price"] = be_stop
        trade["breakeven_active"] = True

    # TRAIL: only after unrealized_r reached trail_start (not immediately after breakeven)
    trail_start = float(regime_cfg.get("trail_start_r", 999))
    if unrealized_r >= trail_start:
        trail_dist_r = float(regime_cfg.get("trail_distance_r", 0.5))
        stop_dist = float(trade.get("stop_distance", 0.0) or 0.0)
        direction = str(trade.get("direction", "long")).lower()
        if stop_dist > 0:
            if direction == "short":
                new_stop = float(trade.get("low_since_entry", trade.get("entry_price", 0.0))) + (
                    trail_dist_r * stop_dist
                )
                if new_stop < float(trade.get("stop_price", new_stop)):
                    trade["stop_price"] = new_stop
            else:
                new_stop = float(trade.get("high_since_entry", trade.get("entry_price", 0.0))) - (
                    trail_dist_r * stop_dist
                )
                if new_stop > float(trade.get("stop_price", new_stop)):
                    trade["stop_price"] = new_stop
        trade["trail_active"] = True
        trade["trail_distance_r"] = trail_dist_r

    # Runner overlay: wider chandelier band once in trail territory (prop-style asymmetry)
    runner_pct = float(regime_cfg.get("runner_allocation_pct", 0.0) or 0.0)
    runner_trail = float(regime_cfg.get("runner_trail_distance_r", 0.0) or 0.0)
    stop_dist = float(trade.get("stop_distance", 0.0) or 0.0)
    if (
        runner_pct > 0
        and runner_trail > 0
        and unrealized_r >= trail_start
        and stop_dist > 0
    ):
        trade["runner_active"] = True
        direction = str(trade.get("direction", "long")).lower()
        cur = float(trade.get("stop_price", 0.0))
        if direction == "short":
            runner_stop = float(trade.get("low_since_entry", trade.get("entry_price", 0.0))) + (
                runner_trail * stop_dist
            )
            # Looser for short = higher stop price
            trade["stop_price"] = max(cur, runner_stop)
        else:
            runner_stop = float(trade.get("high_since_entry", trade.get("entry_price", 0.0))) - (
                runner_trail * stop_dist
            )
            # Looser for long = lower stop price
            trade["stop_price"] = min(cur, runner_stop)

    if regime in {"trend_up", "trend_down"} and unrealized_r < 1.2:
        return "HOLD"
    return str(regime_cfg.get("action", "HOLD"))
