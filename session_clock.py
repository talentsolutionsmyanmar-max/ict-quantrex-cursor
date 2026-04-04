"""
Kill-zone clock from strategy/spec.yaml — UTC recurring windows (v1: no cross-midnight).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from strategy.load_spec import get_kill_zones


def _parse_hhmm(s: str) -> tuple[int, int]:
    parts = str(s).strip().split(":")
    h = int(parts[0])
    m = int(parts[1]) if len(parts) > 1 else 0
    return h, m


def _minutes(h: int, m: int) -> int:
    return h * 60 + m


def _in_window(now_min: int, sh: int, sm: int, eh: int, em: int) -> bool:
    start = _minutes(sh, sm)
    end = _minutes(eh, em)
    if start <= end:
        return start <= now_min <= end
    return now_min >= start or now_min <= end


def get_session_state(now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    now_min = now.hour * 60 + now.minute
    zones_cfg = get_kill_zones()
    active: List[str] = []
    windows: List[Dict[str, Any]] = []

    for z in zones_cfg:
        if not isinstance(z, dict):
            continue
        name = z.get("name", "?")
        try:
            sh, sm = _parse_hhmm(z.get("utc_start", "00:00"))
            eh, em = _parse_hhmm(z.get("utc_end", "23:59"))
        except (ValueError, TypeError):
            continue
        windows.append({"name": name, "utc_start": z.get("utc_start"), "utc_end": z.get("utc_end")})
        if _in_window(now_min, sh, sm, eh, em):
            active.append(name)

    return {
        "utc_iso": now.isoformat(),
        "clock": "UTC",
        "active_kill_zones": active,
        "in_kill_zone": len(active) > 0,
        "kill_zone_windows": windows,
    }
