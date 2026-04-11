"""Unit checks for kill-zone min_signal_strength overlay (UTC minute grid)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from session_clock import (  # noqa: E402
    build_kill_zone_min_strength_overlay,
    effective_min_signal_strength_for_minute,
)


def main() -> None:
    zones = [
        {
            "name": "asian_test",
            "utc_start": "00:00",
            "utc_end": "04:00",
            "min_signal_strength": 75,
        }
    ]
    ov = build_kill_zone_min_strength_overlay(zones)
    assert effective_min_signal_strength_for_minute(60, 0.0, ov) == 75.0, "01:00 UTC inside Asian"
    assert effective_min_signal_strength_for_minute(5 * 60, 0.0, ov) == 0.0, "05:00 UTC outside"

    overlap = [
        {"name": "a", "utc_start": "10:00", "utc_end": "12:00", "min_signal_strength": 60},
        {"name": "b", "utc_start": "11:00", "utc_end": "13:00", "min_signal_strength": 80},
    ]
    ov2 = build_kill_zone_min_strength_overlay(overlap)
    m_1130 = 11 * 60 + 30
    assert effective_min_signal_strength_for_minute(m_1130, 0.0, ov2) == 80.0, "overlap takes max floor"

    base = 50.0
    assert effective_min_signal_strength_for_minute(m_1130, base, ov2) == 80.0
    assert effective_min_signal_strength_for_minute(9 * 60, base, ov2) == base, "no zone -> global min"

    print("session_strength_overlay: ok")


if __name__ == "__main__":
    main()
