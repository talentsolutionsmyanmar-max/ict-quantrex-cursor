"""
Pre-trade risk gate (Phase A stub — expand for live OMS).
Paper mode: permissive. Live: enforce caps from spec + env.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from market_gates import evaluate_entry_gates
from strategy.load_spec import get_gates, read_raw_spec


def _in_kill_zone() -> bool:
    try:
        from session_clock import get_session_state

        return bool(get_session_state().get("in_kill_zone"))
    except Exception:
        return False


class RiskEngine:
    def __init__(self, config: Any):
        self.config = config

    def check_kill_switch(self, mode: str = "PAPER") -> Tuple[bool, str]:
        gates = get_gates()
        # Global halt for real orders only; paper keeps running for convergence drills.
        if gates.get("kill_switch") and str(mode).upper() == "LIVE":
            return False, "Kill switch ON (strategy/spec.yaml gates.kill_switch) — LIVE halted"
        return True, "ok"

    def allow_new_risk(
        self,
        *,
        mode: str,
        symbol: str,
        estimated_notional_usd: float = 0.0,
    ) -> Tuple[bool, List[str]]:
        reasons: List[str] = []
        ok, msg = self.check_kill_switch(mode)
        if not ok:
            return False, [msg]

        raw = read_raw_spec()
        risk = raw.get("risk") or {}
        max_notional = float(risk.get("max_position_notional_usd") or 1e12)
        gates = get_gates()

        if str(mode).upper() == "LIVE":
            if gates.get("require_kill_zone_for_live") and not _in_kill_zone():
                return False, ["LIVE blocked: outside kill zone (gates.require_kill_zone_for_live)"]
            if not getattr(self.config, "BINANCE_API_KEY", ""):
                return False, ["LIVE blocked: no BINANCE_API_KEY"]
            if estimated_notional_usd > max_notional:
                return False, [f"Notional {estimated_notional_usd} exceeds max_position_notional_usd {max_notional}"]
            g_ok, g_msgs = self.check_entry_gates(symbol)
            if not g_ok:
                return False, g_msgs

        reasons.append("pre_trade_ok")
        return True, reasons

    def check_entry_gates(self, symbol: str) -> Tuple[bool, List[str]]:
        """Per-symbol spot liquidity, futures funding, BTC correlation (spec gates.*)."""
        raw = read_raw_spec()
        gates = get_gates()
        return evaluate_entry_gates(symbol=symbol, gates=gates, config=self.config, raw_spec=raw)
