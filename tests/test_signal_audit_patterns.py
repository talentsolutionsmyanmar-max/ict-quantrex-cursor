from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from monitoring.signal_audit import merge_pattern_flags, pattern_flags_from_row


def test_pattern_flags_from_series() -> None:
    row = pd.Series(
        {
            "bullish_fvg": True,
            "bearish_fvg": False,
            "bullish_sweep": False,
            "bearish_sweep": True,
        }
    )
    assert pattern_flags_from_row(row) == {"fvg_detected": True, "sweep_detected": True}


def test_merge_pattern_flags_on_audit_dict() -> None:
    row = pd.Series({"bullish_fvg": False, "bearish_fvg": False, "bullish_sweep": False, "bearish_sweep": False})
    out = merge_pattern_flags({"decision": "SKIP", "skip_reason": "no_signal"}, row)
    assert out["fvg_detected"] is False
    assert out["sweep_detected"] is False


def test_legacy_audit_rows_parse() -> None:
    root = Path(__file__).resolve().parents[1]
    audit = root / "data" / "signal_audit.jsonl"
    if not audit.is_file():
        return
    sample = json.loads(audit.read_text(encoding="utf-8").splitlines()[0])
    assert "ts" in sample
