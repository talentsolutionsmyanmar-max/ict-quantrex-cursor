from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import requests
from dotenv import load_dotenv
import pandas as pd

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.getenv("SUPABASE_ANON_KEY", "").strip()
HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=minimal",
}
_FALLBACK = Path("data/paper_trades_fallback.jsonl")


def _write_fallback_jsonl(data: Dict[str, Any]) -> None:
    _FALLBACK.parent.mkdir(parents=True, exist_ok=True)
    with _FALLBACK.open("a", encoding="utf-8") as f:
        f.write(json.dumps(data) + "\n")


def _normalized_payload(trade: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "trade_id": str(trade.get("id") or trade.get("trade_id") or f"t_{int(datetime.now(timezone.utc).timestamp())}"),
        "symbol": str(trade.get("symbol") or ""),
        "regime": str(trade.get("regime") or "unknown"),
        "side": str(trade.get("side") or ""),
        "entry_price": float(trade.get("entry_price") or 0.0),
        "exit_price": float(trade.get("exit_price") or 0.0),
        "r_multiple": round(float(trade.get("r_multiple") or 0.0), 3),
        "exit_reason": str(trade.get("exit_reason") or trade.get("exit_type") or "UNKNOWN"),
        "pnl_usd": round(float(trade.get("pnl_usd") or trade.get("pnl") or 0.0), 2),
        "timestamp": str(trade.get("timestamp") or trade.get("exit_time") or datetime.now(timezone.utc).isoformat()),
        "paper_mode": bool(trade.get("paper_mode", True)),
    }


def log_trade_to_supabase(trade: Dict[str, Any]) -> Dict[str, Any]:
    payload = _normalized_payload(trade)
    if not SUPABASE_URL or not SUPABASE_KEY:
        _write_fallback_jsonl({"mode": "fallback", "reason": "missing_env", **payload})
        return {"ok": False, "fallback": True, "reason": "missing_env"}

    try:
        res = requests.post(f"{SUPABASE_URL}/rest/v1/live_trades", json=payload, headers=HEADERS, timeout=5)
        if res.status_code in (200, 201):
            return {"ok": True, "fallback": False}
        _write_fallback_jsonl(
            {"mode": "fallback", "reason": f"http_{res.status_code}", "body": res.text[:200], **payload}
        )
        return {"ok": False, "fallback": True, "reason": f"http_{res.status_code}"}
    except Exception as e:
        _write_fallback_jsonl({"mode": "fallback", "reason": str(e), **payload})
        return {"ok": False, "fallback": True, "reason": str(e)}


def fetch_recent_trades(limit: int = 5) -> List[Dict[str, Any]]:
    if SUPABASE_URL and SUPABASE_KEY:
        try:
            url = f"{SUPABASE_URL}/rest/v1/live_trades?select=*&order=timestamp.desc&limit={int(limit)}"
            res = requests.get(url, headers=HEADERS, timeout=5)
            if res.status_code == 200:
                return list(res.json() or [])
        except Exception:
            pass
    if not _FALLBACK.is_file():
        return []
    rows: List[Dict[str, Any]] = []
    for line in _FALLBACK.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    rows = sorted(rows, key=lambda x: str(x.get("timestamp", "")), reverse=True)
    return rows[: int(limit)]


def get_live_trades_row_count() -> int:
    if SUPABASE_URL and SUPABASE_KEY:
        try:
            url = f"{SUPABASE_URL}/rest/v1/live_trades?select=id"
            res = requests.get(url, headers=HEADERS, timeout=5)
            if res.status_code == 200:
                return int(len(res.json() or []))
        except Exception:
            pass
    if not _FALLBACK.is_file():
        return 0
    return sum(1 for _ in _FALLBACK.open("r", encoding="utf-8"))


def calculate_24h_paper_pnl() -> float:
    now = datetime.now(timezone.utc)
    total = 0.0
    for r in fetch_recent_trades(limit=500):
        try:
            ts = pd.to_datetime(r.get("timestamp"), utc=True, errors="coerce")
            if pd.isna(ts):
                continue
            age = (now - ts.to_pydatetime()).total_seconds()
            if age <= 86400:
                total += float(r.get("pnl_usd") or 0.0)
        except Exception:
            continue
    return float(round(total, 2))


if __name__ == "__main__":
    sample = {
        "id": f"rest_test_{int(datetime.now(timezone.utc).timestamp())}",
        "symbol": "BTCUSDT",
        "regime": "trend_down",
        "side": "SHORT",
        "entry_price": 77000,
        "exit_price": 76950,
        "r_multiple": 0.2,
        "exit_reason": "REST_LOGGER_SELFTEST",
        "pnl_usd": 25,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "paper_mode": True,
    }
    result = log_trade_to_supabase(sample)
    print(f"rest_logger_test={result}")
