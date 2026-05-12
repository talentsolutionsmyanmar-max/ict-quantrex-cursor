#!/usr/bin/env python3
"""Portfolio v2 runner with directive pass/fail checks."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backtester import Backtester
from config import build_config


def _strip_frames(out: dict) -> dict:
    cleaned = dict(out)
    ps = cleaned.get("per_symbol")
    if isinstance(ps, dict):
        out_ps = {}
        for sym, payload in ps.items():
            if not isinstance(payload, dict):
                out_ps[sym] = payload
                continue
            d = dict(payload)
            d.pop("df", None)
            d.pop("equity_curve", None)
            out_ps[sym] = d
        cleaned["per_symbol"] = out_ps
    return cleaned


def aggregate_by_regime(per_symbol: dict) -> dict:
    out = {}
    if not isinstance(per_symbol, dict):
        return out
    for sym, payload in per_symbol.items():
        trades = (payload or {}).get("trades") if isinstance(payload, dict) else []
        if not isinstance(trades, list):
            continue
        for t in trades:
            if not isinstance(t, dict):
                continue
            regime = str(t.get("entry_regime_state") or "unknown")
            row = out.setdefault(regime, {"trades": 0, "r_values": [], "pnl_win": 0.0, "pnl_loss": 0.0})
            row["trades"] += 1
            r = float(t.get("r_multiple") or 0.0)
            row["r_values"].append(r)
            pnl = float(t.get("pnl") or 0.0)
            if pnl > 0:
                row["pnl_win"] += pnl
            else:
                row["pnl_loss"] += pnl
    agg = {}
    for reg, d in out.items():
        pf = (d["pnl_win"] / abs(d["pnl_loss"])) if d["pnl_loss"] < 0 else 999.0
        exp = sum(d["r_values"]) / max(1, len(d["r_values"]))
        agg[reg] = {"trades": int(d["trades"]), "pf": float(pf), "expectancy_r": float(exp)}
    return agg


def validate_regime_edge(regime_results, min_trades=15, min_expectancy=0.25):
    for reg, data in (regime_results or {}).items():
        if int(data.get("trades", 0)) < int(min_trades):
            return False, f"INSUFFICIENT_SAMPLE: {reg} ({data.get('trades')} trades)"
        if float(data.get("expectancy_r", 0.0)) < float(min_expectancy):
            return False, f"LOW_EXPECTANCY: {reg} ({float(data.get('expectancy_r', 0.0)):.3f}R)"
    return True, "PASS"


def calculate_regime_isolated_pf(per_symbol: dict):
    """Calculate PF per symbol + regime. Active regimes: trend_up, trend_down."""
    results = {}
    if not isinstance(per_symbol, dict):
        return results
    for sym, payload in per_symbol.items():
        trades = (payload or {}).get("trades") if isinstance(payload, dict) else []
        if not isinstance(trades, list):
            continue
        for reg in ["trend_up", "trend_down"]:
            reg_trades = [
                t
                for t in trades
                if isinstance(t, dict) and str(t.get("entry_regime_state") or "unknown") == reg
            ]
            key = f"{sym}_{reg}"
            if len(reg_trades) < 15:
                results[key] = {"status": "INSUFFICIENT_SAMPLE", "trades": len(reg_trades)}
                continue
            wins = [float(t.get("pnl") or 0.0) for t in reg_trades if float(t.get("pnl") or 0.0) > 0]
            losses = [float(t.get("pnl") or 0.0) for t in reg_trades if float(t.get("pnl") or 0.0) <= 0]
            pf = (sum(wins) / abs(sum(losses))) if losses and abs(sum(losses)) > 0 else 999.0
            win_r = [float(t.get("r_multiple") or 0.0) for t in reg_trades if float(t.get("pnl") or 0.0) > 0]
            loss_r = [abs(float(t.get("r_multiple") or 0.0)) for t in reg_trades if float(t.get("pnl") or 0.0) <= 0]
            win_rate = len(wins) / len(reg_trades)
            exp_r = (win_rate * (sum(win_r) / len(win_r) if win_r else 0.0)) - (
                (1 - win_rate) * (sum(loss_r) / len(loss_r) if loss_r else 0.0)
            )
            results[key] = {
                "pf": round(float(pf), 3),
                "expectancy_r": round(float(exp_r), 3),
                "trades": len(reg_trades),
                "status": "PASS" if pf >= 1.3 and exp_r >= 0.25 else "FAIL",
            }
    return results


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT"])
    ap.add_argument("--start-date", default="2024-01-01")
    ap.add_argument("--end-date", default="2026-04-24")
    ap.add_argument("--timeframe", default="15m")
    ap.add_argument("--out", default="reports/portfolio_backtest_latest.json")
    args = ap.parse_args()

    cfg = build_config()
    out = Backtester.run_multi(
        base_config=cfg,
        symbols=[s.upper().replace("/", "") for s in args.symbols],
        timeframe=args.timeframe,
        start_date=args.start_date,
        end_date=args.end_date,
        initial_capital=10000.0,
        verbose=False,
    )
    out_clean = _strip_frames(out)
    p = Path(args.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "request": vars(args),
        "result": out_clean,
    }
    regime_agg = aggregate_by_regime(out_clean.get("per_symbol") or {})
    passed_regime, regime_msg = validate_regime_edge(regime_agg)
    payload["regime_agg"] = regime_agg
    payload["regime_validation"] = {"pass": passed_regime, "reason": regime_msg}
    payload["regime_isolated"] = calculate_regime_isolated_pf(out_clean.get("per_symbol") or {})
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    agg = out_clean.get("aggregate") if isinstance(out_clean.get("aggregate"), dict) else {}
    per_coin = agg.get("per_coin_summary") if isinstance(agg.get("per_coin_summary"), list) else []
    pf_by_symbol = {str(x.get("symbol")): float(x.get("profit_factor", 0.0) or 0.0) for x in per_coin if isinstance(x, dict)}
    dd_ok = abs(float(agg.get("worst_max_drawdown_pct", 0.0) or 0.0)) <= 1.5
    btc_ok = pf_by_symbol.get("BTCUSDT", 0.0) >= 1.25
    eth_ok = pf_by_symbol.get("ETHUSDT", 0.0) >= 1.15
    passed = bool(dd_ok and btc_ok and eth_ok and passed_regime)
    print(
        json.dumps(
            {
                "passed": passed,
                "btc_pf": pf_by_symbol.get("BTCUSDT"),
                "eth_pf": pf_by_symbol.get("ETHUSDT"),
                "worst_dd_pct": agg.get("worst_max_drawdown_pct"),
                "regime_validation": {"pass": passed_regime, "reason": regime_msg},
            },
            indent=2,
        )
    )
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
