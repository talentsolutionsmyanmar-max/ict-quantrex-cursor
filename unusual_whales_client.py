"""
Optional client for Unusual Whales REST API (same surface their MCP server exposes to AI tools).

Docs / anti-hallucination rules: https://unusualwhales.com/skill.md
MCP setup (Cursor, Claude, etc.): https://unusualwhales.com/public-api/mcp

Note: Data is US equities / options–centric. AutoResearchClaw’s default pipeline is crypto (Binance);
use this for equity context, macro flow, or hybrid research — not as a drop-in for BTC klines.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import requests

from config import Config


class UnusualWhalesClient:
    BASE = "https://api.unusualwhales.com"

    def __init__(self, config: Optional[Config] = None):
        cfg = config or Config()
        self._token = (cfg.UNUSUAL_WHALES_API_KEY or "").strip()
        self._client_id = str(cfg.UW_CLIENT_API_ID or "100001")

    @property
    def configured(self) -> bool:
        return bool(self._token)

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "UW-CLIENT-API-ID": self._client_id,
            "Accept": "application/json",
        }

    def get(self, path: str, params: Optional[Dict[str, Any]] = None, timeout: int = 45) -> Dict[str, Any]:
        if not path.startswith("/"):
            path = "/" + path
        url = f"{self.BASE}{path}"
        r = requests.get(url, headers=self._headers(), params=params or {}, timeout=timeout)
        r.raise_for_status()
        return r.json()

    def market_tide(self, interval_5m: bool = False) -> Dict[str, Any]:
        return self.get("/api/market/market-tide", params={"interval_5m": interval_5m})

    def flow_alerts(
        self,
        *,
        ticker_symbol: Optional[str] = None,
        limit: int = 15,
        min_premium: Optional[int] = None,
        is_otm: Optional[bool] = None,
    ) -> Dict[str, Any]:
        p: Dict[str, Any] = {"limit": limit}
        if ticker_symbol:
            p["ticker_symbol"] = ticker_symbol.upper()
        if min_premium is not None:
            p["min_premium"] = min_premium
        if is_otm is not None:
            p["is_otm"] = is_otm
        return self.get("/api/option-trades/flow-alerts", params=p)

    def flow_recent(self, ticker: str) -> Dict[str, Any]:
        t = ticker.upper().strip()
        return self.get(f"/api/stock/{t}/flow-recent")

    def darkpool_ticker(self, ticker: str) -> Dict[str, Any]:
        t = ticker.upper().strip()
        return self.get(f"/api/darkpool/{t}")
