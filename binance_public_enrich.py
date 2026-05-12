"""
Free Binance public REST snapshots for paper OPEN playbook JSON.

No API keys. Uses spot aggTrades (taker buy/sell proxy) and USDT-M futures
premiumIndex + open interest history. Not equivalent to MMT /vd — a practical
measurement substitute when paid MMT is unavailable.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import requests


def _futures_rest_root(futures_api_v1_url: str) -> str:
    """BINANCE_FUTURES_API is typically https://fapi.binance.com/fapi/v1 -> https://fapi.binance.com"""
    s = str(futures_api_v1_url or "").strip().rstrip("/")
    if s.endswith("/fapi/v1"):
        return s[: -len("/fapi/v1")]
    if "/fapi/" in s:
        return s.split("/fapi/")[0]
    return "https://fapi.binance.com"


def _agg_trades_flow(
    *,
    symbol: str,
    spot_base_url: str,
    limit: int,
    timeout_sec: float,
) -> Dict[str, Any]:
    sym = str(symbol or "").upper().replace("/", "")
    lim = max(50, min(int(limit), 1000))
    base = str(spot_base_url or "").strip().rstrip("/")
    url = f"{base}/aggTrades"
    out: Dict[str, Any] = {"provider": "binance_spot_aggTrades", "symbol": sym, "limit": lim}
    try:
        r = requests.get(url, params={"symbol": sym, "limit": lim}, timeout=float(timeout_sec))
        out["http_status"] = int(r.status_code)
        if r.status_code >= 400:
            out["error"] = r.text[:400]
            return out
        rows = r.json()
    except Exception as e:
        out["error"] = str(e)
        return out

    if not isinstance(rows, list) or not rows:
        out["error"] = "empty_aggTrades"
        return out

    buy_q = 0.0
    sell_q = 0.0
    n = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            p = float(row.get("p"))
            q = float(row.get("q"))
        except (TypeError, ValueError):
            continue
        quote = p * q
        # isBuyerMaker true => buyer is maker => aggressive seller (taker sell)
        m = row.get("m")
        if m is True:
            sell_q += quote
        else:
            buy_q += quote
        n += 1

    tot = buy_q + sell_q
    derived: Dict[str, Any] = {
        "taker_buy_quote_usd": round(buy_q, 2),
        "taker_sell_quote_usd": round(sell_q, 2),
        "trade_count": n,
    }
    if tot > 0:
        derived["buy_vol_share_proxy"] = round(buy_q / tot, 6)
    out["derived"] = derived
    return out


def _premium_index(
    *,
    symbol: str,
    futures_root: str,
    timeout_sec: float,
) -> Dict[str, Any]:
    sym = str(symbol or "").upper().replace("/", "")
    url = f"{futures_root.rstrip('/')}/fapi/v1/premiumIndex"
    out: Dict[str, Any] = {"provider": "binance_usdm_premiumIndex", "symbol": sym}
    try:
        r = requests.get(url, params={"symbol": sym}, timeout=float(timeout_sec))
        out["http_status"] = int(r.status_code)
        if r.status_code >= 400:
            out["error"] = r.text[:400]
            return out
        data = r.json()
    except Exception as e:
        out["error"] = str(e)
        return out

    if not isinstance(data, dict):
        out["error"] = "unexpected_premium_shape"
        return out

    try:
        mark = float(data.get("markPrice") or 0.0)
        idx = float(data.get("indexPrice") or 0.0)
        fr = float(data.get("lastFundingRate") or 0.0)
    except (TypeError, ValueError):
        out["error"] = "parse_premium_fields"
        return out

    basis_bps = None
    if idx > 0 and mark > 0:
        basis_bps = round((mark - idx) / idx * 10000.0, 4)

    out["markPrice"] = mark
    out["indexPrice"] = idx
    out["lastFundingRate"] = fr
    out["basis_bps_vs_index"] = basis_bps
    return out


def _open_interest_hist(
    *,
    symbol: str,
    futures_root: str,
    period: str,
    limit: int,
    timeout_sec: float,
) -> Dict[str, Any]:
    sym = str(symbol or "").upper().replace("/", "")
    lim = max(2, min(int(limit), 30))
    per = str(period or "5m").strip()
    url = f"{futures_root.rstrip('/')}/futures/data/openInterestHist"
    out: Dict[str, Any] = {
        "provider": "binance_usdm_openInterestHist",
        "symbol": sym,
        "period": per,
        "limit": lim,
    }
    try:
        r = requests.get(url, params={"symbol": sym, "period": per, "limit": lim}, timeout=float(timeout_sec))
        out["http_status"] = int(r.status_code)
        if r.status_code >= 400:
            out["error"] = r.text[:400]
            return out
        rows = r.json()
    except Exception as e:
        out["error"] = str(e)
        return out

    if not isinstance(rows, list) or len(rows) < 2:
        out["error"] = "insufficient_oi_points"
        return out

    try:
        o0 = float(rows[-2].get("sumOpenInterest") or 0.0)
        o1 = float(rows[-1].get("sumOpenInterest") or 0.0)
    except (TypeError, ValueError, KeyError, IndexError):
        out["error"] = "parse_oi_rows"
        return out

    out["sumOpenInterest_prev"] = o0
    out["sumOpenInterest_last"] = o1
    out["sumOpenInterest_delta"] = round(o1 - o0, 6)
    return out


def binance_free_entry_enrichment(
    *,
    symbol: str,
    spot_base_url: str,
    futures_api_v1_url: str,
    agg_trades_limit: int = 400,
    timeout_sec: float = 1.5,
    oi_period: str = "5m",
    oi_hist_limit: int = 3,
    include: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Single-call bundle for playbook JSON. Sections are best-effort; failures are isolated.

    include: optional subset of keys {"flow", "premium", "oi"} — default all.
    """
    sym = str(symbol or "").upper().replace("/", "")
    inc = set(str(x).lower() for x in include) if include else {"flow", "premium", "oi"}
    tmo = max(0.3, min(float(timeout_sec), 10.0))
    root = _futures_rest_root(futures_api_v1_url)

    out: Dict[str, Any] = {"ok": True, "symbol": sym, "sections": {}}

    if "flow" in inc:
        out["sections"]["flow"] = _agg_trades_flow(
            symbol=sym,
            spot_base_url=spot_base_url,
            limit=int(agg_trades_limit),
            timeout_sec=tmo,
        )
    if "premium" in inc:
        out["sections"]["premium"] = _premium_index(symbol=sym, futures_root=root, timeout_sec=tmo)
    if "oi" in inc:
        out["sections"]["open_interest"] = _open_interest_hist(
            symbol=sym,
            futures_root=root,
            period=oi_period,
            limit=int(oi_hist_limit),
            timeout_sec=tmo,
        )

    # ok if at least one section has no "error" key (or has derived / numeric payload)
    any_good = False
    for _k, sec in out["sections"].items():
        if isinstance(sec, dict) and "error" not in sec:
            any_good = True
            break
    if not any_good and out["sections"]:
        out["ok"] = False

    hints: Dict[str, Any] = {}
    flow = out["sections"].get("flow")
    if isinstance(flow, dict) and isinstance(flow.get("derived"), dict):
        d = flow["derived"]
        if "buy_vol_share_proxy" in d:
            hints["buy_vol_share_proxy"] = d.get("buy_vol_share_proxy")
        if "trade_count" in d:
            hints["agg_trade_count"] = d.get("trade_count")
    prem = out["sections"].get("premium")
    if isinstance(prem, dict) and "error" not in prem:
        if prem.get("lastFundingRate") is not None:
            hints["lastFundingRate"] = prem.get("lastFundingRate")
        if prem.get("basis_bps_vs_index") is not None:
            hints["basis_bps_vs_index"] = prem.get("basis_bps_vs_index")
    oi = out["sections"].get("open_interest")
    if isinstance(oi, dict) and "error" not in oi:
        if oi.get("sumOpenInterest_delta") is not None:
            hints["sumOpenInterest_delta"] = oi.get("sumOpenInterest_delta")
    if hints:
        out["measurement_hints"] = hints

    return out
