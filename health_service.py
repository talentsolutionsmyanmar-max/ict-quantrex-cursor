"""
Exchange + spec observability for institutional-style monitoring (Phase A).
"""

from __future__ import annotations

import time
from typing import Any, Dict

import requests

from strategy.load_spec import get_spec_meta, read_raw_spec


def _ping(url: str, timeout: float = 4.0) -> tuple[bool, float | None]:
    t0 = time.perf_counter()
    try:
        r = requests.get(url, timeout=timeout)
        ok = r.status_code == 200
        ms = (time.perf_counter() - t0) * 1000.0
        return ok, ms
    except Exception:
        return False, None


def get_health_snapshot(spot_base: str) -> Dict[str, Any]:
    base = (spot_base or "https://api.binance.com/api/v3").rstrip("/")
    spot_time_url = f"{base}/time"
    spot_ok, spot_ms = _ping(spot_time_url)

    fut_ok, fut_ms = _ping("https://fapi.binance.com/fapi/v1/ping")

    meta = get_spec_meta()
    raw = read_raw_spec()
    gates = raw.get("gates") or {}

    return {
        "ok": spot_ok,
        "spec": meta,
        "gates": {
            "kill_switch": bool(gates.get("kill_switch")),
            "require_kill_zone_for_live": bool(gates.get("require_kill_zone_for_live")),
        },
        "binance_spot": {"reachable": spot_ok, "latency_ms": round(spot_ms, 1) if spot_ms is not None else None},
        "binance_usdm_futures": {"reachable": fut_ok, "latency_ms": round(fut_ms, 1) if fut_ms is not None else None},
    }
