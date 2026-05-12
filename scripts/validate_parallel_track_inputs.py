#!/usr/bin/env python3
"""Validate normalized track JSON files before running comparison."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple


REQUIRED_TOP = ("track", "source", "window", "request", "metrics")
REQUIRED_WINDOW = ("start", "end")
REQUIRED_METRICS = ("profit_factor", "max_drawdown", "expectancy", "total_trades", "unique_entries")
PLACEHOLDER_MARKERS = (
    "Fill with exact assumptions used in Vibe run.",
    "Paste the Vibe prompt or strategy id here.",
    "vibe-trading-x.y.z",
)


def _read(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _check_required(payload: Dict[str, Any], path: Path) -> List[str]:
    errs: List[str] = []
    for k in REQUIRED_TOP:
        if k not in payload:
            errs.append(f"{path.name}: missing top-level field `{k}`")
    w = payload.get("window")
    if isinstance(w, dict):
        for k in REQUIRED_WINDOW:
            if k not in w:
                errs.append(f"{path.name}: missing window field `{k}`")
    else:
        errs.append(f"{path.name}: `window` must be an object")

    m = payload.get("metrics")
    if isinstance(m, dict):
        for k in REQUIRED_METRICS:
            if k not in m:
                errs.append(f"{path.name}: missing metrics field `{k}`")
    else:
        errs.append(f"{path.name}: `metrics` must be an object")
    return errs


def _check_placeholders(payload: Dict[str, Any], path: Path) -> List[str]:
    errs: List[str] = []
    raw = json.dumps(payload, ensure_ascii=False)
    for marker in PLACEHOLDER_MARKERS:
        if marker in raw:
            errs.append(f"{path.name}: contains template placeholder text `{marker}`")
    return errs


def _parity_check(baseline: Dict[str, Any], other: Dict[str, Any], baseline_name: str, other_name: str) -> List[str]:
    errs: List[str] = []
    bw = baseline.get("window") or {}
    ow = other.get("window") or {}
    if isinstance(bw, dict) and isinstance(ow, dict):
        if bw.get("start") != ow.get("start") or bw.get("end") != ow.get("end"):
            errs.append(
                f"{other_name}: window mismatch vs {baseline_name} "
                f"({ow.get('start')}..{ow.get('end')} != {bw.get('start')}..{bw.get('end')})"
            )

    # Optional parity hints for symbol/timeframe if present in both requests.
    br = baseline.get("request") if isinstance(baseline.get("request"), dict) else {}
    orq = other.get("request") if isinstance(other.get("request"), dict) else {}
    if br.get("timeframe") and orq.get("timeframe") and br.get("timeframe") != orq.get("timeframe"):
        errs.append(f"{other_name}: timeframe mismatch vs {baseline_name}")
    return errs


def validate(baseline_path: Path, candidate_paths: List[Path]) -> Tuple[List[str], List[str]]:
    errs: List[str] = []
    warns: List[str] = []
    baseline = _read(baseline_path)

    errs.extend(_check_required(baseline, baseline_path))
    errs.extend(_check_placeholders(baseline, baseline_path))

    for p in candidate_paths:
        payload = _read(p)
        errs.extend(_check_required(payload, p))
        errs.extend(_check_placeholders(payload, p))
        errs.extend(_parity_check(baseline, payload, baseline_path.name, p.name))

        m = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {}
        dd = m.get("max_drawdown")
        if isinstance(dd, (int, float)) and dd < 0:
            warns.append(f"{p.name}: max_drawdown is negative ({dd}); ensure units are percent magnitude.")
    return errs, warns


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate normalized track files for fair QuantRex vs Vibe comparison.")
    ap.add_argument("--baseline", required=True, help="Normalized QuantRex baseline JSON")
    ap.add_argument("--candidates", nargs="+", required=True, help="Candidate normalized JSON files")
    args = ap.parse_args()

    baseline_path = Path(args.baseline)
    candidate_paths = [Path(x) for x in args.candidates]
    errs, warns = validate(baseline_path, candidate_paths)

    if warns:
        print("Warnings:")
        for w in warns:
            print(f"- {w}")

    if errs:
        print("Validation failed:")
        for e in errs:
            print(f"- {e}")
        return 2

    print("Validation passed: all track files are structurally valid and parity-aligned.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
