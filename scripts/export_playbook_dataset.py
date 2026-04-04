"""
Export a supervised dataset from playbook events (OPEN + exit events).

This is the first step of a Karpathy-style loop:
  collect -> label -> analyze/train -> propose config change -> verify OOS -> accept/reject

Output: JSONL (one example per closed trade) under data/training/
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


ROOT = Path(__file__).resolve().parents[1]
PLAYBOOK_DB = ROOT / "data" / "playbook.sqlite3"
OUT_DIR = ROOT / "data" / "training"


EXIT_EVENT_TYPES = {
    "TP1",
    "TP2",
    "TP3",
    "STOP_LOSS",
    "TRAIL_STOP",
    "TIME_EXIT",
    "SIGNAL_REVERSAL",
    "STOP_SESSION",
}


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(str(PLAYBOOK_DB))
    c.row_factory = sqlite3.Row
    return c


def _iter_events(*, limit: Optional[int] = None) -> Iterable[Dict[str, Any]]:
    if not PLAYBOOK_DB.exists():
        return []
    q = "SELECT * FROM playbook_events ORDER BY id ASC"
    params: Tuple[Any, ...] = ()
    if limit is not None:
        q += " LIMIT ?"
        params = (int(limit),)
    with _conn() as c:
        rows = c.execute(q, params).fetchall()
    return [dict(r) for r in rows]


def _safe_json_load(s: Optional[str]) -> Optional[Dict[str, Any]]:
    if not s:
        return None
    try:
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


@dataclass
class OpenPos:
    event_id: int
    created_at: str
    mode: str
    symbol: str
    timeframe: str
    side: str
    entry_price: float
    bar_time: Optional[str]
    entry_reason: Optional[Dict[str, Any]]


def _feature_vector(entry_reason: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    # Keep it simple + stable: only keys we know exist from playbook_reason.entry_snapshot.
    r = entry_reason or {}
    flags = r.get("confluence_flags") if isinstance(r.get("confluence_flags"), dict) else {}

    def _f(key: str, default: float = 0.0) -> float:
        try:
            return float(r.get(key, default))
        except Exception:
            return float(default)

    def _i(key: str, default: int = 0) -> int:
        try:
            return int(r.get(key, default))
        except Exception:
            return int(default)

    def _bflag(key: str) -> int:
        try:
            return 1 if bool(flags.get(key, False)) else 0
        except Exception:
            return 0

    return {
        "signal_strength": _f("signal_strength", 0.0),
        "confluence": _i("confluence", 0),
        "min_strength_required": _f("min_strength_required", 0.0),
        "min_confluence_required": _i("min_confluence_required", 0),
        # Common confluence booleans (present in entry_reason directly).
        "bullish_sweep": 1 if r.get("bullish_sweep") else 0,
        "bearish_sweep": 1 if r.get("bearish_sweep") else 0,
        "bullish_fvg": 1 if r.get("bullish_fvg") else 0,
        "bearish_fvg": 1 if r.get("bearish_fvg") else 0,
        "discount": 1 if r.get("discount") else 0,
        "premium": 1 if r.get("premium") else 0,
        # Extended flags (from ict_execution.confluence_breakdown when present).
        "flag_ote_hit": _bflag("ote_hit"),
        "flag_fvg": _bflag("fvg"),
        "flag_liquidity_sweep": _bflag("liquidity_sweep"),
        "flag_discount_premium": _bflag("discount_premium"),
        "flag_session_ok": _bflag("session_ok"),
    }


def export_dataset(*, out_path: Path, limit: Optional[int] = None) -> Dict[str, Any]:
    events = list(_iter_events(limit=limit))
    if not events:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text("", encoding="utf-8")
        return {"success": True, "examples": 0, "note": "no playbook events found"}

    stacks: Dict[Tuple[str, str], List[OpenPos]] = {}  # (symbol,timeframe) -> FIFO open positions
    examples = 0

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for ev in events:
            et = str(ev.get("event_type") or "").upper()
            symbol = str(ev.get("symbol") or "").upper()
            tf = str(ev.get("timeframe") or "")
            key = (symbol, tf)

            if et == "OPEN":
                try:
                    ep = float(ev.get("entry_price"))
                except Exception:
                    continue
                op = OpenPos(
                    event_id=int(ev.get("id") or 0),
                    created_at=str(ev.get("created_at") or ""),
                    mode=str(ev.get("mode") or ""),
                    symbol=symbol,
                    timeframe=tf,
                    side=str(ev.get("side") or ""),
                    entry_price=ep,
                    bar_time=ev.get("bar_time"),
                    entry_reason=_safe_json_load(ev.get("entry_reason_json")),
                )
                stacks.setdefault(key, []).append(op)
                continue

            if et in EXIT_EVENT_TYPES:
                # Pair exit with earliest unmatched OPEN (FIFO) for that symbol/timeframe.
                if not stacks.get(key):
                    continue
                op = stacks[key].pop(0)
                pnl = ev.get("pnl")
                try:
                    pnl_f = float(pnl) if pnl is not None else 0.0
                except Exception:
                    pnl_f = 0.0

                ex = {
                    "paired_open_event_id": op.event_id,
                    "paired_exit_event_id": int(ev.get("id") or 0),
                    "mode": op.mode,
                    "symbol": op.symbol,
                    "timeframe": op.timeframe,
                    "side": op.side,
                    "entry_price": op.entry_price,
                    "exit_price": ev.get("exit_price"),
                    "exit_type": et,
                    "pnl": pnl_f,
                    "label_win": 1 if pnl_f > 0 else 0,
                    "created_at_open": op.created_at,
                    "created_at_exit": str(ev.get("created_at") or ""),
                    "bar_time_open": op.bar_time,
                    "bar_time_exit": ev.get("bar_time"),
                    "features": _feature_vector(op.entry_reason),
                }
                f.write(json.dumps(ex, default=str) + "\n")
                examples += 1

    return {"success": True, "examples": int(examples), "out_path": str(out_path)}


def main() -> None:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out = OUT_DIR / f"playbook_examples_{ts}.jsonl"
    res = export_dataset(out_path=out, limit=None)
    print(json.dumps(res, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

