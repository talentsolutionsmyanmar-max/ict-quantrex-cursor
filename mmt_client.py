"""
Thin client for MMT Market Data API (https://docs.mmt.gg/api).

Security: call only from the backend. Never expose MMT_API_KEY to the browser.
"""

from __future__ import annotations

import os
import re
import time
from typing import Any, Dict, List, Optional

import requests

_DEFAULT_HOST = "https://eu-central-1.mmt.gg"
_EXCHANGE_RE = re.compile(r"^[a-z0-9]{2,32}(:[a-z0-9]{2,32})*$")
_SYMBOL_RE = re.compile(r"^[a-z0-9]{2,10}/[a-z0-9]{2,10}$")

# Simple process-wide circuit: avoid hammering MMT after failures (e.g. Myanmar latency).
_MMT_CIRCUIT_OPEN_UNTIL = 0.0
_CIRCUIT_COOLDOWN_SEC = 30.0


def mmt_api_key() -> str:
    return (os.getenv("MMT_API_KEY", "") or "").strip()


def mmt_base_url() -> str:
    return (os.getenv("MMT_API_HOST", _DEFAULT_HOST) or _DEFAULT_HOST).rstrip("/")


def mmt_configured() -> bool:
    return bool(mmt_api_key())


def _headers() -> Dict[str, str]:
    return {"X-API-Key": mmt_api_key(), "Accept": "application/json"}


def _get(path: str, params: Dict[str, Any], *, timeout_sec: float = 20.0) -> requests.Response:
    url = f"{mmt_base_url()}{path}"
    return requests.get(url, headers=_headers(), params=params, timeout=timeout_sec)


def venue_ticker_to_unified(symbol: str) -> Optional[str]:
    """
    Map Binance-style spot tickers to MMT unified symbols (e.g. BTCUSDT -> btc/usd).
    See https://docs.mmt.gg/api/basics/symbols
    """
    s = str(symbol or "").upper().replace("/", "")
    if s.endswith("USDT"):
        return f"{s[:-4].lower()}/usd"
    if s.endswith("USDC"):
        return f"{s[:-4].lower()}/usd"
    if s.endswith("USD"):
        return f"{s[:-3].lower()}/usd"
    return None


def _open_mmt_circuit() -> None:
    global _MMT_CIRCUIT_OPEN_UNTIL
    _MMT_CIRCUIT_OPEN_UNTIL = time.time() + _CIRCUIT_COOLDOWN_SEC


def _mmt_circuit_blocks() -> bool:
    return time.time() < float(_MMT_CIRCUIT_OPEN_UNTIL)


def validate_exchange_symbol(exchange: str, symbol: str) -> Optional[str]:
    ex = str(exchange or "").strip().lower()
    sym = str(symbol or "").strip().lower()
    if not _EXCHANGE_RE.match(ex):
        return "Invalid exchange parameter."
    if not _SYMBOL_RE.match(sym):
        return "Invalid symbol (use unified format like btc/usd)."
    return None


def fetch_candles(
    *,
    exchange: str,
    symbol: str,
    tf: str,
    frm: int,
    to: int,
) -> Dict[str, Any]:
    err = validate_exchange_symbol(exchange, symbol)
    if err:
        return {"success": False, "error": err}
    r = _get(
        "/api/v1/candles",
        {"exchange": exchange, "symbol": symbol, "tf": tf, "from": int(frm), "to": int(to)},
    )
    try:
        data = r.json()
    except Exception:
        data = {"raw": r.text[:2000]}
    if r.status_code >= 400:
        return {"success": False, "status": r.status_code, "error": data}
    return {"success": True, "status": r.status_code, "data": data}


def fetch_orderbook(*, exchange: str, symbol: str, levels: int = 200) -> Dict[str, Any]:
    err = validate_exchange_symbol(exchange, symbol)
    if err:
        return {"success": False, "error": err}
    lv = max(10, min(int(levels), 2000))
    r = _get("/api/v1/orderbook", {"exchange": exchange, "symbol": symbol, "levels": lv})
    try:
        data = r.json()
    except Exception:
        data = {"raw": r.text[:2000]}
    if r.status_code >= 400:
        return {"success": False, "status": r.status_code, "error": data}
    return {"success": True, "status": r.status_code, "data": data}


def fetch_stats(
    *,
    exchange: str,
    symbol: str,
    tf: str,
    frm: int,
    to: int,
    timeout_sec: float = 20.0,
) -> Dict[str, Any]:
    err = validate_exchange_symbol(exchange, symbol)
    if err:
        return {"success": False, "error": err}
    r = _get(
        "/api/v1/stats",
        {"exchange": exchange, "symbol": symbol, "tf": tf, "from": int(frm), "to": int(to)},
        timeout_sec=float(timeout_sec),
    )
    try:
        data = r.json()
    except Exception:
        data = {"raw": r.text[:2000]}
    if r.status_code >= 400:
        return {"success": False, "status": r.status_code, "error": data}
    return {"success": True, "status": r.status_code, "data": data}


def fetch_stats_entry_enrichment(
    *,
    exchange: str,
    venue_symbol: str,
    timeout_sec: float = 0.8,
    tf: str = "1m",
    lookback_sec: int = 420,
    include: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Short-window GET /api/v1/stats for playbook enrichment on OPEN.

    MMT stats schema: https://docs.mmt.gg/api/rest/stats
    """
    if not mmt_configured():
        return {"ok": False, "skipped": True, "reason": "no_mmt_api_key"}
    if _mmt_circuit_blocks():
        return {"ok": False, "skipped": True, "reason": "circuit_open"}

    uni = venue_ticker_to_unified(venue_symbol)
    if not uni:
        return {"ok": False, "skipped": True, "reason": "unsupported_venue_symbol", "venue_symbol": venue_symbol}

    ex = str(exchange or "").strip().lower()
    err = validate_exchange_symbol(ex, uni)
    if err:
        return {"ok": False, "skipped": True, "reason": err}

    keys = list(include) if include else ["vb", "vs", "tb", "ts", "sk", "fr", "lb", "ls"]
    now = int(time.time())
    lb = max(60, min(int(lookback_sec), 3600))
    frm = now - lb
    to = now

    try:
        out = fetch_stats(exchange=ex, symbol=uni, tf=str(tf), frm=frm, to=to, timeout_sec=float(timeout_sec))
        if not out.get("success"):
            _open_mmt_circuit()
            return {"ok": False, "skipped": True, "reason": "mmt_stats_error", "detail": out}
        payload = out.get("data") or {}
        rows = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(rows, list) or not rows:
            return {"ok": True, "skipped": True, "reason": "empty_stats_series", "meta": payload}
        last = rows[-1]
        if not isinstance(last, dict):
            return {"ok": False, "skipped": True, "reason": "unexpected_row_shape"}

        latest: Dict[str, Any] = {}
        for k in keys:
            if k not in last:
                continue
            v = last.get(k)
            if k == "sk" and isinstance(v, list):
                latest[k] = list(v)
                if v:
                    latest["skew_last_band"] = float(v[-1])
            else:
                latest[k] = v

        vb = float(last.get("vb") or 0.0)
        vs = float(last.get("vs") or 0.0)
        tot = vb + vs
        derived: Dict[str, Any] = {}
        if tot > 0:
            derived["buy_vol_share"] = round(vb / tot, 6)

        return {
            "ok": True,
            "exchange": ex,
            "unified_symbol": uni,
            "venue_symbol": str(venue_symbol).upper().replace("/", ""),
            "tf": str(tf),
            "from": frm,
            "to": to,
            "points": int(payload.get("points") or len(rows)),
            "latest": latest,
            "derived": derived,
        }
    except Exception as e:
        _open_mmt_circuit()
        return {"ok": False, "skipped": True, "reason": "exception", "error": str(e)}


def fetch_vd(
    *,
    exchange: str,
    symbol: str,
    tf: str,
    frm: int,
    to: int,
    bucket: int,
    timeout_sec: float = 20.0,
) -> Dict[str, Any]:
    """GET /api/v1/vd — https://docs.mmt.gg/api/rest/vd"""
    err = validate_exchange_symbol(exchange, symbol)
    if err:
        return {"success": False, "error": err}
    b = int(bucket)
    if b < 1 or b > 11:
        return {"success": False, "error": "bucket must be 1..11"}
    r = _get(
        "/api/v1/vd",
        {
            "exchange": exchange,
            "symbol": symbol,
            "tf": tf,
            "from": int(frm),
            "to": int(to),
            "bucket": b,
        },
        timeout_sec=float(timeout_sec),
    )
    try:
        data = r.json()
    except Exception:
        data = {"raw": r.text[:2000]}
    if r.status_code >= 400:
        return {"success": False, "status": r.status_code, "error": data}
    return {"success": True, "status": r.status_code, "data": data}


def fetch_vd_entry_enrichment(
    *,
    exchange: str,
    venue_symbol: str,
    timeout_sec: float = 0.8,
    tf: str = "1m",
    lookback_sec: int = 420,
    bucket: int = 1,
) -> Dict[str, Any]:
    """
    Short-window GET /api/v1/vd for cumulative volume-delta OHLC on OPEN.

    Docs: https://docs.mmt.gg/api/rest/vd (bucket 1 = all trades; basic tiers often bucket 1 only).
    """
    if not mmt_configured():
        return {"ok": False, "skipped": True, "reason": "no_mmt_api_key"}
    if _mmt_circuit_blocks():
        return {"ok": False, "skipped": True, "reason": "circuit_open"}

    uni = venue_ticker_to_unified(venue_symbol)
    if not uni:
        return {"ok": False, "skipped": True, "reason": "unsupported_venue_symbol", "venue_symbol": venue_symbol}

    ex = str(exchange or "").strip().lower()
    err = validate_exchange_symbol(ex, uni)
    if err:
        return {"ok": False, "skipped": True, "reason": err}

    b = max(1, min(int(bucket), 11))
    now = int(time.time())
    lb = max(60, min(int(lookback_sec), 3600))
    frm = now - lb
    to = now

    try:
        out = fetch_vd(
            exchange=ex,
            symbol=uni,
            tf=str(tf),
            frm=frm,
            to=to,
            bucket=b,
            timeout_sec=float(timeout_sec),
        )
        if not out.get("success"):
            _open_mmt_circuit()
            return {"ok": False, "skipped": True, "reason": "mmt_vd_error", "detail": out}
        payload = out.get("data") or {}
        rows = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(rows, list) or not rows:
            return {"ok": True, "skipped": True, "reason": "empty_vd_series", "meta": payload}
        last = rows[-1]
        if not isinstance(last, dict):
            return {"ok": False, "skipped": True, "reason": "unexpected_row_shape"}

        prev = rows[-2] if len(rows) >= 2 and isinstance(rows[-2], dict) else None
        derived: Dict[str, Any] = {}
        try:
            c0 = float(last.get("c") or 0.0)
            derived["vd_close"] = c0
            if prev is not None:
                c1 = float(prev.get("c") or 0.0)
                derived["vd_delta_1bar"] = round(c0 - c1, 6)
        except (TypeError, ValueError):
            pass

        return {
            "ok": True,
            "exchange": ex,
            "unified_symbol": uni,
            "venue_symbol": str(venue_symbol).upper().replace("/", ""),
            "tf": str(tf),
            "bucket": b,
            "from": frm,
            "to": to,
            "points": int(payload.get("points") or len(rows)),
            "latest_bar": {k: last.get(k) for k in ("t", "o", "h", "l", "c", "n") if k in last},
            "derived": derived,
        }
    except Exception as e:
        _open_mmt_circuit()
        return {"ok": False, "skipped": True, "reason": "exception", "error": str(e)}
