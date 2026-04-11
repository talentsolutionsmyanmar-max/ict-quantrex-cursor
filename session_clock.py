"""
Kill-zone clock from strategy/spec.yaml — UTC recurring windows (v1: no cross-midnight).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from strategy.load_spec import get_kill_zones, read_raw_spec

# Minute-of-day UTC (0..1439) -> max(min_signal_strength) among active zones with that key, or None
KillZoneStrengthOverlay = List[Optional[float]]


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


def build_kill_zone_min_strength_overlay(zones_cfg: Optional[List[Dict[str, Any]]] = None) -> KillZoneStrengthOverlay:
    """
    For each UTC minute-of-day, return the strictest min_signal_strength among kill zones
    active at that minute (if any zone defines the key). None means no overlay beyond global config.
    """
    zones_cfg = zones_cfg if zones_cfg is not None else get_kill_zones()
    overlay: KillZoneStrengthOverlay = [None] * 1440
    for m in range(1440):
        floors: List[float] = []
        for z in zones_cfg:
            if not isinstance(z, dict):
                continue
            if z.get("min_signal_strength") is None:
                continue
            try:
                sh, sm = _parse_hhmm(z.get("utc_start", "00:00"))
                eh, em = _parse_hhmm(z.get("utc_end", "23:59"))
            except (ValueError, TypeError):
                continue
            if _in_window(m, sh, sm, eh, em):
                try:
                    floors.append(float(z["min_signal_strength"]))
                except (TypeError, ValueError):
                    continue
        overlay[m] = max(floors) if floors else None
    return overlay


def effective_min_signal_strength_for_minute(
    minute_of_day_utc: int,
    global_min: float,
    overlay: Optional[KillZoneStrengthOverlay] = None,
) -> float:
    ov = overlay if overlay is not None else build_kill_zone_min_strength_overlay()
    m = int(minute_of_day_utc) % 1440
    zf = ov[m]
    if zf is None:
        return float(global_min)
    return max(float(global_min), float(zf))


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

    raw = read_raw_spec()
    sess = raw.get("sessions") if isinstance(raw.get("sessions"), dict) else {}
    return {
        "utc_iso": now.isoformat(),
        "clock": str(sess.get("clock", "UTC")),
        "timezone_mode": sess.get("timezone_mode"),
        "timezone_source": sess.get("timezone_source"),
        "dst_adjust": sess.get("dst_adjust"),
        "active_kill_zones": active,
        "in_kill_zone": len(active) > 0,
        "kill_zone_windows": windows,
    }
