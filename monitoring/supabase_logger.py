from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from dotenv import load_dotenv
import pandas as pd

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.getenv("SUPABASE_ANON_KEY", "").strip()
_CLIENT = None
_ERR = None
_FALLBACK = Path(__file__).resolve().parents[1] / "reports" / "supabase_fallback_live_trades.jsonl"

try:
    if SUPABASE_URL and SUPABASE_KEY:
        from supabase import create_client  # type: ignore

        _CLIENT = create_client(SUPABASE_URL, SUPABASE_KEY)
except Exception as e:  # pragma: no cover
    _ERR = str(e)
    _CLIENT = None


def _append_fallback(payload: Dict[str, Any]) -> None:
    _FALLBACK.parent.mkdir(parents=True, exist_ok=True)
    with _FALLBACK.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload) + "\n")


def _normalize_trade(trade: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "trade_id": str(trade.get("trade_id") or trade.get("id") or ""),
        "symbol": str(trade.get("symbol") or ""),
        "regime": str(trade.get("regime") or "unknown"),
        "entry_price": float(trade.get("entry_price") or 0.0),
        "exit_price": float(trade.get("exit_price") or 0.0),
        "r_multiple": round(float(trade.get("r_multiple") or 0.0), 3),
        "exit_reason": str(trade.get("exit_reason") or trade.get("exit_type") or "UNKNOWN"),
        "pnl_usd": round(float(trade.get("pnl_usd") or trade.get("pnl") or 0.0), 2),
        "timestamp": str(trade.get("timestamp") or trade.get("exit_time") or datetime.now(timezone.utc).isoformat()),
        "paper_mode": bool(trade.get("paper_mode", True)),
    }


def log_trade_to_supabase(trade: Dict[str, Any]) -> Dict[str, Any]:
    row = _normalize_trade(trade)
    if _CLIENT is None:
        _append_fallback({"mode": "fallback", "reason": _ERR or "missing_env", **row})
        return {"ok": False, "fallback": True, "reason": _ERR or "missing_env"}
    try:
        _CLIENT.table("live_trades").insert(row).execute()
        return {"ok": True, "fallback": False}
    except Exception as e:  # pragma: no cover
        _append_fallback({"mode": "fallback", "reason": str(e), **row})
        return {"ok": False, "fallback": True, "reason": str(e)}


def get_live_trades_row_count() -> int:
    if _CLIENT is None:
        if not _FALLBACK.is_file():
            return 0
        return sum(1 for _ in _FALLBACK.open("r", encoding="utf-8"))
    try:
        res = _CLIENT.table("live_trades").select("id", count="exact").limit(1).execute()
        return int(getattr(res, "count", 0) or 0)
    except Exception:
        return 0


def fetch_recent_trades(limit: int = 5) -> list[dict]:
    if _CLIENT is None:
        if not _FALLBACK.is_file():
            return []
        rows = []
        for line in _FALLBACK.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
        rows = sorted(rows, key=lambda x: str(x.get("timestamp", "")), reverse=True)
        return rows[: int(limit)]
    try:
        res = (
            _CLIENT.table("live_trades")
            .select("*")
            .order("timestamp", desc=True)
            .limit(int(limit))
            .execute()
        )
        return list(getattr(res, "data", []) or [])
    except Exception:
        return []


def calculate_24h_paper_pnl() -> float:
    now = datetime.now(timezone.utc)
    rows = fetch_recent_trades(limit=500)
    pnl = 0.0
    for r in rows:
        try:
            ts = pd.to_datetime(r.get("timestamp"), utc=True, errors="coerce")
            if pd.isna(ts):
                continue
            if (now - ts.to_pydatetime()).total_seconds() <= 86400:
                pnl += float(r.get("pnl_usd") or 0.0)
        except Exception:
            continue
    return float(round(pnl, 2))
