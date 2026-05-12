from typing import Any, Dict, Tuple


def should_open_position(current_regime: str, config_universe: Dict[str, Any]) -> Tuple[bool, str]:
    """HARD BLOCK: Engine-level regime enforcement."""
    allowed = config_universe.get("allowed_regimes", []) if isinstance(config_universe, dict) else []
    banned = config_universe.get("banned_regimes", []) if isinstance(config_universe, dict) else []
    state = str(current_regime or "unknown")

    if state in banned:
        return False, f"BLOCKED_BY_REGIME: {state}"
    if allowed and state not in allowed:
        return False, f"BLOCKED_BY_REGIME: {state}"
    return True, "ALLOWED"
