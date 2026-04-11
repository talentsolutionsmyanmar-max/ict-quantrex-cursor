"""
YAML-driven pre-trade market gates: spot 24h quote liquidity, futures funding cap,
BTC return correlation. Public Binance endpoints only; TTL cache to limit request rate.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import requests

# (key -> (expires_epoch, payload))
_cache: Dict[str, Tuple[float, Any]] = {}
_cache_lock = threading.Lock()
_DEFAULT_TTL = 60.0


def _cache_get(key: str, ttl_sec: float, fetch: Any) -> Any:
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
        if hit is not None and hit[0] > now:
            return hit[1]
    val = fetch()
    with _cache_lock:
        _cache[key] = (now + max(5.0, float(ttl_sec)), val)
    return val


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"Accept": "application/json"})
    return s


def fetch_quote_volume_24h(spot_base_url: str, symbol: str, ttl_sec: float = _DEFAULT_TTL) -> float:
    """USDT quote volume over 24h for a spot symbol (Binance ticker/24hr)."""

    sym = str(symbol).upper().replace("/", "")

    def _fetch() -> float:
        url = f"{str(spot_base_url).rstrip('/')}/ticker/24hr"
        r = _session().get(url, params={"symbol": sym}, timeout=15)
        r.raise_for_status()
        j = r.json()
        if not isinstance(j, dict):
            return 0.0
        qv = j.get("quoteVolume")
        return float(qv or 0.0)

    return float(_cache_get(f"qv24|{sym}", ttl_sec, _fetch))


def fetch_abs_funding_rate(futures_base_url: str, symbol: str, ttl_sec: float = _DEFAULT_TTL) -> Optional[float]:
    """Absolute last funding rate for USDT-M perpetual (same symbol name as spot)."""

    sym = str(symbol).upper().replace("/", "")
    base = str(futures_base_url).rstrip("/")

    def _fetch() -> Optional[float]:
        url = f"{base}/premiumIndex"
        r = _session().get(url, params={"symbol": sym}, timeout=15)
        if r.status_code != 200:
            return None
        j = r.json()
        if not isinstance(j, dict):
            return None
        fr = j.get("lastFundingRate")
        if fr is None:
            return None
        return abs(float(fr))

    return _cache_get(f"fund|{sym}", ttl_sec, _fetch)  # type: ignore[return-value]


def _fetch_klines_close(
    spot_base_url: str,
    symbol: str,
    interval: str,
    limit: int,
) -> pd.Series:
    url = f"{str(spot_base_url).rstrip('/')}/klines"
    r = _session().get(
        url,
        params={"symbol": str(symbol).upper().replace("/", ""), "interval": interval, "limit": int(limit)},
        timeout=20,
    )
    r.raise_for_status()
    rows = r.json()
    if not isinstance(rows, list) or not rows:
        return pd.Series(dtype=float)
    closes = [float(x[4]) for x in rows]
    return pd.Series(closes)


def btc_return_correlation(
    spot_base_url: str,
    symbol: str,
    btc_symbol: str,
    interval: str,
    limit: int,
    ttl_sec: float = _DEFAULT_TTL,
) -> Optional[float]:
    """Pearson correlation of log returns vs BTC over last `limit` bars."""

    sym = str(symbol).upper().replace("/", "")
    btc = str(btc_symbol).upper().replace("/", "")
    if sym == btc:
        return 0.0

    key = f"corr|{sym}|{btc}|{interval}|{limit}"

    def _fetch() -> Optional[float]:
        ca = _fetch_klines_close(spot_base_url, sym, interval, limit)
        cb = _fetch_klines_close(spot_base_url, btc, interval, limit)
        n = min(len(ca), len(cb))
        if n < 30:
            return None
        merged = pd.DataFrame(
            {
                "a": ca.iloc[-n:].astype(float).values,
                "b": cb.iloc[-n:].astype(float).values,
            }
        )
        ret = merged.pct_change().dropna()
        if len(ret) < 20:
            return None
        return float(ret["a"].corr(ret["b"]))

    return _cache_get(key, ttl_sec, _fetch)  # type: ignore[return-value]


def evaluate_entry_gates(
    *,
    symbol: str,
    gates: Dict[str, Any],
    config: Any,
    raw_spec: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, List[str]]:
    """
    Apply gates.min_liquidity_usd, gates.max_funding_rate, gates.correlation_cap_btc.
    On HTTP/parse errors: fail-open (allow) with a warning reason — avoids bricking paper on blips.
    """
    reasons: List[str] = []
    sym = str(symbol).upper().replace("/", "")
    if not gates or not isinstance(gates, dict):
        return True, ["no_gates"]

    ttl = 60.0
    if raw_spec and isinstance(raw_spec.get("gates"), dict):
        t = raw_spec["gates"].get("market_gate_cache_ttl_sec")
        if t is not None:
            ttl = float(t)

    spot_base = getattr(config, "BINANCE_API", "https://api.binance.com/api/v3")
    fut_base = getattr(config, "BINANCE_FUTURES_API", "https://fapi.binance.com/fapi/v1")

    btc_sym = "BTCUSDT"
    if raw_spec:
        m = raw_spec.get("market")
        if isinstance(m, dict) and m.get("symbol"):
            btc_sym = str(m.get("symbol")).upper().replace("/", "")

    min_liq = gates.get("min_liquidity_usd")
    if min_liq is not None:
        try:
            thr = float(min_liq)
            if thr > 0:
                qv = fetch_quote_volume_24h(spot_base, sym, ttl_sec=ttl)
                if qv < thr:
                    return False, [f"min_liquidity_usd: 24h quote volume {qv:,.0f} USDT < {thr:,.0f}"]
                reasons.append("liquidity_ok")
        except Exception as e:
            reasons.append(f"liquidity_gate_warn:{type(e).__name__}")

    max_fund = gates.get("max_funding_rate")
    if max_fund is not None:
        try:
            cap = float(max_fund)
            if cap >= 0:
                fr = fetch_abs_funding_rate(fut_base, sym, ttl_sec=ttl)
                if fr is None:
                    reasons.append("funding_gate_skip:no_futures_data")
                elif fr > cap:
                    return False, [f"max_funding_rate: |funding|={fr:.6f} > cap {cap:.6f}"]
                else:
                    reasons.append("funding_ok")
        except Exception as e:
            reasons.append(f"funding_gate_warn:{type(e).__name__}")

    cap_corr = gates.get("correlation_cap_btc")
    if cap_corr is not None and sym != btc_sym:
        try:
            lim = float(cap_corr)
            if 0 < lim < 1.0:
                kl = 200
                if raw_spec and isinstance(raw_spec.get("gates"), dict):
                    k = raw_spec["gates"].get("correlation_klines_limit")
                    if k is not None:
                        kl = int(k)
                tf = getattr(config, "TIMEFRAME", "15m")
                rho = btc_return_correlation(spot_base, sym, btc_sym, tf, kl, ttl_sec=ttl)
                if rho is None:
                    reasons.append("correlation_gate_skip:insufficient_data")
                elif abs(rho) > lim:
                    return False, [f"correlation_cap_btc: |rho|={abs(rho):.3f} > {lim:.3f} vs {btc_sym}"]
                else:
                    reasons.append("correlation_ok")
        except Exception as e:
            reasons.append(f"correlation_gate_warn:{type(e).__name__}")

    if not reasons:
        reasons.append("market_gates_pass")
    return True, reasons
