#!/usr/bin/env python3
"""Compare QuantRex and Vibe research tracks with explicit promotion gates."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List


@dataclass
class Gates:
    min_unique_entries: int = 60
    min_profit_factor: float = 1.20
    max_drawdown_pct: float = 8.0
    min_expectancy: float = 0.0


def _read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _get_metrics(payload: Dict[str, Any]) -> Dict[str, Any]:
    m = payload.get("metrics") or {}
    total = float(m.get("total_trades") or 0.0)
    unique_entries = float(m.get("unique_entries") or total)
    return {
        "profit_factor": float(m.get("profit_factor") or 0.0),
        "max_drawdown": float(m.get("max_drawdown") or 0.0),
        "expectancy": float(m.get("expectancy") or 0.0),
        "total_trades": total,
        "unique_entries": unique_entries,
        "win_rate": m.get("win_rate"),
        "total_pnl": m.get("total_pnl"),
    }


def _passes(m: Dict[str, Any], g: Gates) -> List[str]:
    reasons: List[str] = []
    if m["unique_entries"] < g.min_unique_entries:
        reasons.append(f"unique_entries {m['unique_entries']} < {g.min_unique_entries}")
    if m["profit_factor"] < g.min_profit_factor:
        reasons.append(f"profit_factor {m['profit_factor']:.2f} < {g.min_profit_factor:.2f}")
    if m["max_drawdown"] > g.max_drawdown_pct:
        reasons.append(f"max_drawdown {m['max_drawdown']:.2f}% > {g.max_drawdown_pct:.2f}%")
    if m["expectancy"] <= g.min_expectancy:
        reasons.append(f"expectancy {m['expectancy']:.4f} <= {g.min_expectancy:.4f}")
    return reasons


def _render_table(rows: List[Dict[str, Any]]) -> str:
    header = (
        "track".ljust(22)
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
            str(r["track"])[:22].ljust(22)
            + f"{m['unique_entries']:.0f}".rjust(10)
            + f"{m['profit_factor']:.2f}".rjust(8)
            + f"{m['max_drawdown']:.2f}".rjust(8)
            + f"{m['expectancy']:.4f}".rjust(10)
            + f"{(m['total_pnl'] if m['total_pnl'] is not None else 0):.2f}".rjust(12)
        )
    return "\n".join(out)


def main() -> int:
    p = argparse.ArgumentParser(description="Compare research tracks under shared promotion gates.")
    p.add_argument(
        "--input",
        nargs="+",
        required=True,
        help="JSON result files (each file has track + metrics).",
    )
    p.add_argument("--min-entries", type=int, default=60)
    p.add_argument("--min-pf", type=float, default=1.20)
    p.add_argument("--max-dd", type=float, default=8.0, help="Max drawdown percent.")
    p.add_argument("--min-exp", type=float, default=0.0)
    args = p.parse_args()

    gates = Gates(
        min_unique_entries=int(args.min_entries),
        min_profit_factor=float(args.min_pf),
        max_drawdown_pct=float(args.max_dd),
        min_expectancy=float(args.min_exp),
    )

    rows: List[Dict[str, Any]] = []
    for raw_path in args.input:
        path = Path(raw_path)
        payload = _read_json(path)
        track = str(payload.get("track") or path.stem)
        metrics = _get_metrics(payload)
        failures = _passes(metrics, gates)
        rows.append({"track": track, "metrics": metrics, "failures": failures})

    rows_sorted = sorted(
        rows,
        key=lambda r: (
            len(r["failures"]),
            -r["metrics"]["profit_factor"],
            r["metrics"]["max_drawdown"],
            -r["metrics"]["expectancy"],
        ),
    )
    print(_render_table(rows_sorted))
    print("\nPromotion gates:")
    print(
        f"- entries>={gates.min_unique_entries}, PF>={gates.min_profit_factor:.2f}, "
        f"DD<={gates.max_drawdown_pct:.2f}%, expectancy>{gates.min_expectancy:.4f}"
    )

    print("\nPass/fail:")
    for r in rows_sorted:
        if not r["failures"]:
            print(f"- PASS  {r['track']}")
        else:
            print(f"- FAIL  {r['track']} :: " + "; ".join(r["failures"]))

    winner = next((r for r in rows_sorted if not r["failures"]), None)
    if winner:
        print(f"\nTop promotable track: {winner['track']}")
        return 0
    print("\nNo track passes current gates.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
