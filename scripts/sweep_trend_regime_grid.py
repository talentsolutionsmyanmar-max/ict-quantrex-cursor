#!/usr/bin/env python3
"""Grid sweep for trend regime thresholds to improve entries without killing PF."""

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


def _metrics(result: Dict[str, Any]) -> Dict[str, Any]:
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


def _score(m: Dict[str, Any]) -> float:
    # Reward entries, PF, expectancy; penalize high drawdown.
    return (m["unique_entries"] * 0.3) + (m["profit_factor"] * 30) + (m["expectancy"] * 2) - (abs(m["max_drawdown"]) * 1.5)


def main() -> int:
    ap = argparse.ArgumentParser(description="Trend regime threshold grid search")
    ap.add_argument("--start-date", required=True)
    ap.add_argument("--end-date", required=True)
    ap.add_argument("--symbol", default="")
    ap.add_argument("--timeframe", default="")
    ap.add_argument("--spec", default=str(ROOT / "strategy" / "spec.yaml"))
    ap.add_argument("--out", default=str(ROOT / "reports" / "trend_regime_grid.json"))
    args = ap.parse_args()

    base = yaml.safe_load(Path(args.spec).read_text(encoding="utf-8")) or {}
    trend_strength_values = [74, 76, 78]
    trend_confluence_values = [3, 4]
    range_strength_values = [72, 74, 76]
    range_confluence_values = [2, 3]

    tmp = ROOT / "reports" / "spec_variants"
    tmp.mkdir(parents=True, exist_ok=True)
    old = os.environ.get("STRATEGY_SPEC_PATH", "")
    rows: List[Dict[str, Any]] = []

    try:
        for ts in trend_strength_values:
            for tc in trend_confluence_values:
                for rs in range_strength_values:
                    for rc in range_confluence_values:
                        raw = copy.deepcopy(base)
                        reg = raw.setdefault("regime", {})
                        reg["range_min_signal_strength"] = ts if rs is None else rs
                        reg["range_min_confluence"] = rc
                        ra = reg.setdefault("regime_actions", {})
                        for k in ("trend_up", "trend_down"):
                            blk = ra.get(k)
                            if isinstance(blk, dict):
                                blk["min_signal_strength"] = ts
                                blk["min_confluence"] = tc

                        name = f"ts{ts}_tc{tc}_rs{rs}_rc{rc}"
                        sp = tmp / f"{name}.yaml"
                        sp.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
                        os.environ["STRATEGY_SPEC_PATH"] = str(sp)

                        cfg = build_config()
                        cfg.BACKTEST_START_DATE = args.start_date
                        cfg.BACKTEST_END_DATE = args.end_date
                        if args.symbol:
                            cfg.SYMBOL = str(args.symbol).upper().replace("/", "")
                        if args.timeframe:
                            cfg.TIMEFRAME = str(args.timeframe)
                        r = Backtester(cfg).run(verbose=False)
                        m = _metrics(r)
                        row = {
                            "name": name,
                            "params": {
                                "trend_min_signal_strength": ts,
                                "trend_min_confluence": tc,
                                "range_min_signal_strength": rs,
                                "range_min_confluence": rc,
                            },
                            "metrics": m,
                            "score": _score(m),
                            "spec_path": str(sp),
                        }
                        rows.append(row)
                        print(f"completed: {name}")
    finally:
        if old:
            os.environ["STRATEGY_SPEC_PATH"] = old
        elif "STRATEGY_SPEC_PATH" in os.environ:
            del os.environ["STRATEGY_SPEC_PATH"]

    def _rank_key(r: Dict[str, Any]):
        m = r["metrics"]
        return (-r["score"], -m["unique_entries"], -m["profit_factor"], abs(m["max_drawdown"]))

    ranked = sorted(rows, key=_rank_key)
    out = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "window": {"start": args.start_date, "end": args.end_date},
        "top10": ranked[:10],
        "all_count": len(ranked),
    }
    Path(args.out).write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"saved: {args.out}")
    if ranked:
        top = ranked[0]
        print(json.dumps({"best": top["name"], "params": top["params"], "metrics": top["metrics"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
