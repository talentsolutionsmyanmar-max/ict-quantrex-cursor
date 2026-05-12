#!/usr/bin/env python3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.exit_engine import apply_regime_exit_logic, calculate_r_multiple


def _assert_close(a: float, b: float, eps: float = 1e-9):
    if abs(float(a) - float(b)) > eps:
        raise AssertionError(f"Expected {b}, got {a}")


def run_tests():
    r1 = calculate_r_multiple(100.0, 106.0, 0.05, "long")
    _assert_close(r1, 1.2)

    r2 = calculate_r_multiple(100.0, 95.0, 0.05, "short")
    _assert_close(r2, 1.0)

    t = {"unrealized_r": 1.0}
    cfg = {"trend_up": {"trail_start_r": 1.5, "trail_distance_r": 0.6, "action": "EXIT"}}
    act = apply_regime_exit_logic(t, cfg, "trend_up")
    if act != "HOLD":
        raise AssertionError(f"Expected HOLD under 1.2R trend, got {act}")

    t2 = {"unrealized_r": 1.7}
    act2 = apply_regime_exit_logic(t2, cfg, "trend_up")
    if not t2.get("trail_active"):
        raise AssertionError("Trail should be active at >= trail_start_r")
    if act2 != "EXIT":
        raise AssertionError(f"Expected EXIT from regime action, got {act2}")

    # Runner widens short stop (higher stop = looser)
    t3 = {
        "entry_price": 100.0,
        "stop_price": 104.0,
        "stop_distance": 4.0,
        "direction": "short",
        "unrealized_r": 2.5,
        "high_since_entry": 100.0,
        "low_since_entry": 92.0,
    }
    cfg3 = {
        "trend_down": {
            "trail_start_r": 2.0,
            "trail_distance_r": 0.5,
            "runner_allocation_pct": 0.3,
            "runner_trail_distance_r": 1.2,
        }
    }
    apply_regime_exit_logic(t3, cfg3, "trend_down")
    # Trail-only stop ~ 92 + 0.5*4 = 94; runner widens to max(94, 92 + 1.2*4) = 96.8
    if float(t3["stop_price"]) < 96.0:
        raise AssertionError("Runner overlay should loosen short stop vs trail-only in this fixture")

    print("exit_unit_tests: PASS")


if __name__ == "__main__":
    run_tests()
