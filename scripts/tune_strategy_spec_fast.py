#!/usr/bin/env python3
"""Fast, practical strategy-spec tuning sweep (small curated variant set)."""

from __future__ import annotations

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


def _metrics(result: Dict[str, Any]) -> Dict[str, Any]:
    m = result.get("metrics") if isinstance(result.get("metrics"), dict) else {}
    regime = m.get("regime_summary") if isinstance(m.get("regime_summary"), dict) else {}
    return {
        "profit_factor": _num(m.get("profit_factor")),
        "max_drawdown": _num(m.get("max_drawdown")),
        "expectancy": _num(m.get("expectancy")),
        "total_trades": _num(m.get("total_trades")),
        "unique_entries": _num(regime.get("unique_entries_total"), _num(m.get("total_trades"))),
        "win_rate": _num(m.get("win_rate")),
        "total_pnl": _num(m.get("total_pnl")),
        "sharpe_ratio": _num(m.get("sharpe_ratio")),
    }


def _score(m: Dict[str, Any]) -> float:
    # Prioritize balanced quality: entries up, PF >=1 preferred, drawdown controlled.
    pf_term = (m["profit_factor"] - 1.0) * 40.0
    return (m["unique_entries"] * 0.4) + pf_term + (m["expectancy"] * 2.0) - (abs(m["max_drawdown"]) * 1.5)


def _variants(base: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    out.append({"name": "baseline", "raw": copy.deepcopy(base), "notes": "Current spec"})

    def make(name: str, *, t_sig: int, t_conf: int, r_sig: int, r_conf: int, corr: float) -> Dict[str, Any]:
        raw = copy.deepcopy(base)
        reg = raw.setdefault("regime", {})
        reg["range_min_signal_strength"] = r_sig
        reg["range_min_confluence"] = r_conf
        ra = reg.setdefault("regime_actions", {})
        for key in ("trend_up", "trend_down"):
            blk = ra.get(key)
            if isinstance(blk, dict):
                blk["min_signal_strength"] = t_sig
                blk["min_confluence"] = t_conf
        raw.setdefault("gates", {})["correlation_cap_btc"] = corr
        raw.setdefault("market", {}).setdefault("allocation", {})["correlation_cap"] = corr
        return {"name": name, "raw": raw, "notes": f"t_sig={t_sig} t_conf={t_conf} r_sig={r_sig} r_conf={r_conf} corr={corr}"}

    out.append(make("balanced_1", t_sig=76, t_conf=3, r_sig=74, r_conf=3, corr=0.88))
    out.append(make("balanced_2", t_sig=74, t_conf=3, r_sig=74, r_conf=2, corr=0.88))
    out.append(make("balanced_3", t_sig=76, t_conf=3, r_sig=72, r_conf=2, corr=0.88))
    out.append(make("balanced_4", t_sig=74, t_conf=3, r_sig=72, r_conf=2, corr=0.90))
    out.append(make("quality_guard_1", t_sig=78, t_conf=3, r_sig=74, r_conf=3, corr=0.88))
    out.append(make("quality_guard_2", t_sig=78, t_conf=4, r_sig=74, r_conf=3, corr=0.88))
    out.append(make("throughput_push", t_sig=74, t_conf=2, r_sig=72, r_conf=2, corr=0.90))
    return out


def main() -> int:
    start = "2024-01-01"
    end = "2026-04-24"
    spec_path = ROOT / "strategy" / "spec.yaml"
    base = yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}
    variants = _variants(base)

    old = os.environ.get("STRATEGY_SPEC_PATH", "")
    out_rows: List[Dict[str, Any]] = []
    tmp_dir = ROOT / "reports" / "spec_variants"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    try:
        for v in variants:
            sp = tmp_dir / f"{v['name']}.yaml"
            sp.write_text(yaml.safe_dump(v["raw"], sort_keys=False), encoding="utf-8")
            os.environ["STRATEGY_SPEC_PATH"] = str(sp)
            cfg = build_config()
            cfg.BACKTEST_START_DATE = start
            cfg.BACKTEST_END_DATE = end
            r = Backtester(cfg).run(verbose=False)
            m = _metrics(r)
            out_rows.append(
                {
                    "name": v["name"],
                    "notes": v["notes"],
                    "spec_path": str(sp),
                    "metrics": m,
                    "score": _score(m),
                }
            )
            print(f"completed: {v['name']}")
    finally:
        if old:
            os.environ["STRATEGY_SPEC_PATH"] = old
        elif "STRATEGY_SPEC_PATH" in os.environ:
            del os.environ["STRATEGY_SPEC_PATH"]

    ranked = sorted(out_rows, key=lambda x: (-x["score"], -x["metrics"]["unique_entries"], -x["metrics"]["profit_factor"]))
    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "window": {"start": start, "end": end},
        "ranked": ranked,
    }
    out_path = ROOT / "reports" / "strategy_tune_fast.json"
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"saved: {out_path}")
    print(json.dumps({"best": ranked[0]["name"], "metrics": ranked[0]["metrics"], "notes": ranked[0]["notes"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
