"""
Lightweight top-of-book microstructure snapshot (Binance public REST).

Used to enrich paper-trade playbook JSON with bid/ask imbalance context.
Not a full DOM/heatmap — a minimal, API-key-free perception layer.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

import requests


def _notional_top(levels: List[Tuple[str, str]], n: int) -> float:
    s = 0.0
    for p, q in levels[: max(0, n)]:
        try:
            s += float(p) * float(q)
        except (TypeError, ValueError):
            continue
    return float(s)


def binance_depth_micro_snapshot(
    *,
    symbol: str,
    base_url: str,
    limit: int = 50,
    top_n: int = 20,
    timeout_sec: float = 1.5,
) -> Dict[str, Any]:
    sym = str(symbol or "").upper().replace("/", "")
    lim = max(5, min(int(limit), 500))
    n = max(1, min(int(top_n), lim))
    url = f"{str(base_url or '').rstrip('/')}/depth"
    out: Dict[str, Any] = {"provider": "binance_spot_depth", "symbol": sym, "limit": lim, "top_n": n}
    try:
        r = requests.get(url, params={"symbol": sym, "limit": lim}, timeout=float(timeout_sec))
        out["http_status"] = int(r.status_code)
        if r.status_code >= 400:
            out["error"] = r.text[:500]
            return out
        data = r.json()
    except Exception as e:
        out["error"] = str(e)
        return out

    bids = data.get("bids") or []
    asks = data.get("asks") or []
    if not isinstance(bids, list) or not isinstance(asks, list):
        out["error"] = "unexpected_depth_shape"
        return out

    bid_n = _notional_top([(str(a[0]), str(a[1])) for a in bids if isinstance(a, (list, tuple)) and len(a) >= 2], n)
    ask_n = _notional_top([(str(a[0]), str(a[1])) for a in asks if isinstance(a, (list, tuple)) and len(a) >= 2], n)
    tot = bid_n + ask_n
    imb = (bid_n / tot) if tot > 0 else None
    out["top_bid_notional_usd"] = round(bid_n, 2)
    out["top_ask_notional_usd"] = round(ask_n, 2)
    out["imbalance_bid_share"] = round(float(imb), 4) if imb is not None else None
    return out
