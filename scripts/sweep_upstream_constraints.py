#!/usr/bin/env python3
"""Sweep upstream strategy constraints to diagnose low entry throughput."""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backtester import Backtester
from config import build_config


def _num(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _extract_metrics(result: Dict[str, Any]) -> Dict[str, Any]:
    m = result.get("metrics") if isinstance(result.get("metrics"), dict) else {}
    regime = m.get("regime_summary") if isinstance(m.get("regime_summary"), dict) else {}
    unique_entries = _num(regime.get("unique_entries_total"), _num(m.get("total_trades")))
    return {
        "profit_factor": _num(m.get("profit_factor")),
        "max_drawdown": _num(m.get("max_drawdown")),
        "expectancy": _num(m.get("expectancy")),
        "total_trades": _num(m.get("total_trades")),
        "unique_entries": unique_entries,
        "win_rate": _num(m.get("win_rate")),
        "total_pnl": _num(m.get("total_pnl")),
        "sharpe_ratio": _num(m.get("sharpe_ratio")),
    }


def _set_require_fvg_false(raw: Dict[str, Any]) -> None:
    ra = raw.setdefault("regime", {}).setdefault("regime_actions", {})
    for key in ("ranging", "trend_down", "trend_up"):
        block = ra.get(key)
        if isinstance(block, dict):
            block["require_fvg_confirmation"] = False


def _variant_defs(base: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    out.append({"name": "baseline", "raw": copy.deepcopy(base), "notes": "Current spec"})

    v = copy.deepcopy(base)
    v.setdefault("regime", {})["enabled"] = False
    out.append({"name": "regime_disabled", "raw": v, "notes": "Disable regime gate"})

    v = copy.deepcopy(base)
    for kz in v.setdefault("sessions", {}).get("kill_zones", []) or []:
        if isinstance(kz, dict) and kz.get("min_signal_strength") is not None:
            kz["min_signal_strength"] = 0
    out.append({"name": "session_overlay_relaxed", "raw": v, "notes": "Neutralize kill-zone strength overlay"})

    v = copy.deepcopy(base)
    fvg = v.setdefault("ict", {}).setdefault("fvg", {})
    fvg["confirmation_candles"] = 0
    fvg["mitigation_filter"] = False
    fvg["ignore_mitigated"] = False
    _set_require_fvg_false(v)
    out.append({"name": "fvg_relaxed", "raw": v, "notes": "Remove strict FVG confirmations"})

    v = copy.deepcopy(base)
    reg = v.setdefault("regime", {})
    reg["range_min_signal_strength"] = 68
    reg["range_min_confluence"] = 2
    ra = reg.setdefault("regime_actions", {})
    for k, sig, conf in (("trend_down", 72, 3), ("trend_up", 72, 3)):
        blk = ra.get(k)
        if isinstance(blk, dict):
            blk["min_signal_strength"] = sig
            blk["min_confluence"] = conf
    out.append({"name": "regime_loosened", "raw": v, "notes": "Loosen regime strict minima"})

    v = copy.deepcopy(base)
    v.setdefault("regime", {})["enabled"] = False
    fvg = v.setdefault("ict", {}).setdefault("fvg", {})
    fvg["confirmation_candles"] = 0
    fvg["mitigation_filter"] = False
    fvg["ignore_mitigated"] = False
    _set_require_fvg_false(v)
    for kz in v.setdefault("sessions", {}).get("kill_zones", []) or []:
        if isinstance(kz, dict) and kz.get("min_signal_strength") is not None:
            kz["min_signal_strength"] = 0
    out.append({"name": "combo_relaxed", "raw": v, "notes": "Regime off + FVG relaxed + session overlay off"})
    return out


def _render(rows: List[Dict[str, Any]]) -> str:
    header = (
        "variant".ljust(24)
        + "entries".rjust(10)
        + " PF".rjust(8)
        + " DD%".rjust(8)
        + " exp".rjust(10)
        + " pnl".rjust(12)
    )
    sep = "-" * len(header)
    out = [header, sep]
    for r in rows:
        m = r["metrics"]
        out.append(
            r["name"][:24].ljust(24)
            + f"{m['unique_entries']:.0f}".rjust(10)
            + f"{m['profit_factor']:.2f}".rjust(8)
            + f"{m['max_drawdown']:.2f}".rjust(8)
            + f"{m['expectancy']:.4f}".rjust(10)
            + f"{m['total_pnl']:.2f}".rjust(12)
        )
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description="Sweep upstream constraints to improve trade entries.")
    ap.add_argument("--start-date", required=True)
    ap.add_argument("--end-date", required=True)
    ap.add_argument("--symbol", default="")
    ap.add_argument("--timeframe", default="")
    ap.add_argument("--spec", default=str(ROOT / "strategy" / "spec.yaml"))
    ap.add_argument("--out", default=str(ROOT / "reports" / "upstream_constraint_sweep.json"))
    args = ap.parse_args()

    base_spec = Path(args.spec)
    raw_base = yaml.safe_load(base_spec.read_text(encoding="utf-8")) or {}
    variants = _variant_defs(raw_base)

    tmp_dir = ROOT / "reports" / "spec_variants"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    old_spec_path = os.environ.get("STRATEGY_SPEC_PATH", "")
    rows: List[Dict[str, Any]] = []
    try:
        for v in variants:
            spec_path = tmp_dir / f"{v['name']}.yaml"
            spec_path.write_text(yaml.safe_dump(v["raw"], sort_keys=False), encoding="utf-8")
            os.environ["STRATEGY_SPEC_PATH"] = str(spec_path)

            cfg = build_config()
            cfg.BACKTEST_START_DATE = args.start_date
            cfg.BACKTEST_END_DATE = args.end_date
            if args.symbol:
                cfg.SYMBOL = str(args.symbol).upper().replace("/", "")
            if args.timeframe:
                cfg.TIMEFRAME = str(args.timeframe)

            result = Backtester(cfg).run(verbose=False)
            rows.append({"name": v["name"], "notes": v["notes"], "spec_path": str(spec_path), "metrics": _extract_metrics(result)})
            print(f"completed: {v['name']}")
    finally:
        if old_spec_path:
            os.environ["STRATEGY_SPEC_PATH"] = old_spec_path
        elif "STRATEGY_SPEC_PATH" in os.environ:
            del os.environ["STRATEGY_SPEC_PATH"]

    ranked = sorted(rows, key=lambda r: (-r["metrics"]["unique_entries"], -r["metrics"]["profit_factor"], r["metrics"]["max_drawdown"]))
    print("\n" + _render(ranked))
    report = {"generated_at_utc": datetime.now(timezone.utc).isoformat(), "window": {"start": args.start_date, "end": args.end_date}, "rows": ranked}
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nsaved: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
