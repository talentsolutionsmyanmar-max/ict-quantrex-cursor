"""
Multi-asset DOM snapshot collector (v2.8.0 research).
Writes data/dom_btc_audit.jsonl, dom_eth_audit.jsonl, dom_sol_audit.jsonl.
Independent of live paper loop and config. Does not touch dom_cvd_audit.jsonl.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import ccxt

ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "dom_collector_multi.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("quantrex.multi_dom")

DEFAULT_SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]


class MultiDOMCollector:
    def __init__(self, symbols: list[str], interval_sec: int = 900, depth: int = 20):
        self.symbols = symbols
        self.interval = interval_sec
        self.depth = depth
        self.exchanges = {
            s: ccxt.binance({"enableRateLimit": True, "options": {"defaultType": "spot"}})
            for s in self.symbols
        }
        self.paths = {
            s: ROOT / "data" / f"dom_{s.split('/')[0].lower()}_audit.jsonl"
            for s in self.symbols
        }
        for p in self.paths.values():
            p.parent.mkdir(parents=True, exist_ok=True)
        self.state = {
            s: {
                "prev_bid": 0.0,
                "prev_ask": 0.0,
                "cvd": 0.0,
                "first": True,
            }
            for s in self.symbols
        }
        now = datetime.now(timezone.utc)
        self.last_log = {s: now - timedelta(seconds=interval_sec) for s in self.symbols}
        logger.info("Multi-asset DOM collector: %s | interval=%ss", symbols, interval_sec)

    def _fetch_and_log(self, symbol: str) -> None:
        ex = self.exchanges[symbol]
        path = self.paths[symbol]
        st = self.state[symbol]
        try:
            ob = ex.fetch_order_book(symbol, limit=self.depth)
            bids, asks = ob["bids"], ob["asks"]
            if not bids or not asks:
                return

            bid_vol = sum(float(v) for _, v in bids)
            ask_vol = sum(float(v) for _, v in asks)
            tot = bid_vol + ask_vol
            imb = (bid_vol - ask_vol) / tot if tot > 0 else 0.0
            best_bid = float(bids[0][0])
            best_ask = float(asks[0][0])
            mid = (best_ask + best_bid) / 2
            spread_bps = ((best_ask - best_bid) / mid) * 10000 if mid else 0.0

            cur_bid5 = sum(float(v) for _, v in bids[:5])
            cur_ask5 = sum(float(v) for _, v in asks[:5])

            if st["first"]:
                delta = 0.0
                st["prev_bid"], st["prev_ask"] = cur_bid5, cur_ask5
                st["first"] = False
            else:
                delta = (cur_bid5 - st["prev_bid"]) - (cur_ask5 - st["prev_ask"])
                st["cvd"] += delta
                st["prev_bid"], st["prev_ask"] = cur_bid5, cur_ask5

            snap = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "symbol": symbol.replace("/", "").upper(),
                "price": round(mid, 4),
                "imbalance": round(imb, 4),
                "cvd_delta": round(delta, 2),
                "cvd_cumulative": round(st["cvd"], 2),
                "spread_bps": round(spread_bps, 2),
                "depth_levels": self.depth,
            }

            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(snap, ensure_ascii=False) + "\n")

            tag = symbol.split("/")[0]
            logger.info("%s | Imb: %+.4f | CVDd: %+.2f", tag, imb, delta)
        except Exception as e:
            logger.warning("%s fetch failed: %s", symbol, e)

    async def run(self) -> None:
        try:
            while True:
                now = datetime.now(timezone.utc)
                for s in self.symbols:
                    if (now - self.last_log[s]).total_seconds() >= self.interval:
                        self._fetch_and_log(s)
                        self.last_log[s] = datetime.now(timezone.utc)
                await asyncio.sleep(10)
        except KeyboardInterrupt:
            logger.info("Stopped by user")
        finally:
            for ex in self.exchanges.values():
                close_fn = getattr(ex, "close", None)
                if close_fn:
                    try:
                        res = close_fn()
                        if asyncio.iscoroutine(res):
                            await res
                    except Exception:
                        pass


def main():
    ap = argparse.ArgumentParser(description="Multi-asset DOM collector (research)")
    ap.add_argument(
        "--symbols",
        default=",".join(DEFAULT_SYMBOLS),
        help="Comma-separated ccxt symbols, default BTC,ETH,SOL USDT",
    )
    ap.add_argument("--interval", type=int, default=900, help="Seconds between snapshots per symbol")
    ap.add_argument("--depth", type=int, default=20, help="Order book depth")
    args = ap.parse_args()
    syms = [x.strip() for x in args.symbols.split(",") if x.strip()]
    asyncio.run(MultiDOMCollector(syms, interval_sec=args.interval, depth=args.depth).run())


if __name__ == "__main__":
    main()
