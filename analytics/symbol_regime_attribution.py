#!/usr/bin/env python3
import json
from pathlib import Path

import numpy as np
import pandas as pd


def diagnose_symbol_edge(trades_df, regime_labels=None, symbol="BTCUSDT"):
    """Returns edge attribution per regime for a single symbol."""
    sym_trades = trades_df[trades_df["symbol"] == symbol]
    results = {}
    for regime in ["trend_up", "trend_down", "chop", "high_vol"]:
        mask = sym_trades["regime"] == regime
        reg_trades = sym_trades[mask]
        if reg_trades.empty:
            continue
        wins = reg_trades[reg_trades["pnl"] > 0]
        losses = reg_trades[reg_trades["pnl"] <= 0]
        pf = wins["pnl"].sum() / abs(losses["pnl"].sum()) if len(losses) > 0 and abs(losses["pnl"].sum()) > 0 else np.inf
        win_rate = len(wins) / len(reg_trades)
        win_r = wins["r_mult"].mean() if not wins.empty else 0.0
        loss_r = abs(losses["r_mult"].mean()) if not losses.empty else 0.0
        exp_r = (win_rate * win_r) - ((1 - win_rate) * loss_r)
        results[regime] = {
            "trades": int(len(reg_trades)),
            "pf": round(float(pf), 3),
            "expectancy_r": round(float(exp_r), 3),
            "action": "DISABLE" if pf < 0.8 else "REDUCE" if pf < 1.2 else "SCALE",
        }
    out = Path(f"reports/{symbol.lower()}_edge_attribution.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    return results


def _load_trades_from_portfolio_report(path: Path) -> pd.DataFrame:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = []
    per_symbol = (((payload.get("result") or {}).get("per_symbol")) or {})
    for sym, obj in per_symbol.items():
        trades = (obj or {}).get("trades") or []
        for t in trades:
            if not isinstance(t, dict):
                continue
            rows.append(
                {
                    "symbol": sym,
                    "regime": str(t.get("entry_regime_state") or "unknown"),
                    "pnl": float(t.get("pnl") or 0.0),
                    "r_mult": float(t.get("r_multiple") or 0.0),
                }
            )
    return pd.DataFrame(rows)


if __name__ == "__main__":
    report = Path("reports/portfolio_backtest_latest.json")
    if not report.exists():
        print("portfolio report missing; run scripts/run_portfolio_backtest_v2.py first")
        raise SystemExit(2)
    df = _load_trades_from_portfolio_report(report)
    if df.empty:
        print("no trades found in portfolio report")
        raise SystemExit(2)
    symbols = sorted(df["symbol"].dropna().unique().tolist())
    for s in symbols:
        res = diagnose_symbol_edge(df, symbol=s)
        print(s, json.dumps(res, indent=2))
