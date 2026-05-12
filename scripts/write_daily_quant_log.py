#!/usr/bin/env python3
"""Write daily quant log in required JSON format."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def _load(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def main() -> int:
    reports = Path("reports")
    pf_gate = _load(reports / "symbol_pf_gate_latest.json")
    health = _load(reports / "daily_health.json")
    portfolio = _load(reports / "portfolio_backtest_latest.json")
    agg = ((portfolio.get("result") or {}).get("aggregate") or {}) if isinstance(portfolio, dict) else {}

    pf = float(agg.get("min_profit_factor") or 0.0)
    dd = abs(float(agg.get("worst_max_drawdown_pct") or 0.0))
    exp = 0.0
    entries = int(agg.get("total_trades_all") or 0)

    status = "BLOCKED"
    if pf >= 1.3 and dd <= 2.0 and exp >= 0.25:
        status = "PROMOTE"
    elif pf > 0:
        status = "PROGRESS"

    out = {
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "config_version": "v2.3",
        "metrics": {"pf": round(pf, 4), "dd": round(dd, 4), "expectancy_r": round(exp, 4), "entries": entries},
        "observations": [
            f"health_status={health.get('status', 'UNKNOWN')}",
            f"pf_gate_all_pass={pf_gate.get('all_pass', False)}",
        ],
        "hypotheses": [
            "BTC-specific edge restoration required before scaling frequency",
            "Regime-level symbol disables in weak trend_up improve quality"
        ],
        "next_iteration": "Run BTC-only isolation sweep and enforce PF>=1.3 before shipping any config changes",
        "status": status,
    }
    out_path = reports / "daily_quant_log.json"
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"saved: {out_path}")
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
