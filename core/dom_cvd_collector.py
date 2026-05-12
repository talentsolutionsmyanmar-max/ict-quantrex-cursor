"""
Standalone DOM/CVD snapshot collector (v2.7.2).
Writes to data/dom_cvd_audit.jsonl only. Does not touch config, paper_trader, or app.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import ccxt

LOG_DIR = Path("logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "dom_cvd_collector.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("quantrex.dom_cvd")


class DOMCVDCollector:
    def __init__(self, symbol: str = "BTC/USDT", depth: int = 20, interval_sec: int = 900):
        self.symbol = symbol
        self.depth = depth
        self.interval = interval_sec
        self.exchange = ccxt.binance({"enableRateLimit": True, "options": {"defaultType": "spot"}})
        self.prev_bid_vol = 0.0
        self.prev_ask_vol = 0.0
        self.cvd_cumulative = 0.0
        self._first_snapshot = True
        self.log_path = Path("data/dom_cvd_audit.jsonl")
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        logger.info(
            "DOM/CVD Collector initialized: %s | depth=%s | interval=%ss",
            symbol,
            depth,
            interval_sec,
        )

    def _fetch_order_book_safe(self):
        for attempt in range(3):
            try:
                ob = self.exchange.fetch_order_book(self.symbol, limit=self.depth)
                return ob["bids"], ob["asks"]
            except ccxt.NetworkError as e:
                logger.warning("Network error (attempt %s/3): %s", attempt + 1, e)
                time.sleep(2)
            except ccxt.ExchangeError as e:
                logger.error("Exchange error: %s", e)
                break
            except Exception as e:
                logger.error("Unexpected error: %s", e)
                break
        return [], []

    def _log_snapshot(self, bids, asks):
        if not bids or not asks:
            logger.warning("Empty order book, skipping log")
            return

        try:
            bid_vol = sum(float(v) for _, v in bids)
            ask_vol = sum(float(v) for _, v in asks)
            total_vol = bid_vol + ask_vol
            imbalance = (bid_vol - ask_vol) / total_vol if total_vol > 0 else 0.0

            best_bid = float(bids[0][0])
            best_ask = float(asks[0][0])
            mid_price = (best_bid + best_ask) / 2
            spread_bps = ((best_ask - best_bid) / mid_price) * 10000 if mid_price else 0.0

            cur_bid_top5 = sum(float(v) for _, v in bids[:5])
            cur_ask_top5 = sum(float(v) for _, v in asks[:5])

            if self._first_snapshot:
                delta = 0.0
                self.prev_bid_vol = cur_bid_top5
                self.prev_ask_vol = cur_ask_top5
                self._first_snapshot = False
            else:
                delta = (cur_bid_top5 - self.prev_bid_vol) - (cur_ask_top5 - self.prev_ask_vol)
                self.cvd_cumulative += delta
                self.prev_bid_vol = cur_bid_top5
                self.prev_ask_vol = cur_ask_top5

            avg_depth = total_vol / max(self.depth * 2, 1)
            walls = []
            for side, levels in [("bid", bids), ("ask", asks)]:
                for price, vol in levels:
                    v = float(vol)
                    if v > avg_depth * 2:
                        walls.append(
                            {
                                "side": side,
                                "price": float(price),
                                "vol": v,
                                "x_avg": round(v / avg_depth, 2) if avg_depth else 0.0,
                            }
                        )

            snapshot = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "symbol": self.symbol.replace("/USDT", "USDT"),
                "price": round(mid_price, 2),
                "dom_imbalance": round(imbalance, 4),
                "dom_bid_vol": round(bid_vol, 2),
                "dom_ask_vol": round(ask_vol, 2),
                "dom_walls": walls,
                "cvd_delta": round(delta, 2),
                "cvd_cumulative": round(self.cvd_cumulative, 2),
                "spread_bps": round(spread_bps, 1),
                "depth_levels": self.depth,
            }

            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(snapshot, ensure_ascii=False) + "\n")

            tag = "BULL" if imbalance > 0.1 else "BEAR" if imbalance < -0.1 else "NEUTRAL"
            logger.info(
                "%s @ $%.2f | Imb: %+.4f (%s) | CVDd: %+.2f | Walls: %s | Spread: %.1fbps",
                self.symbol,
                mid_price,
                imbalance,
                tag,
                delta,
                len(walls),
                spread_bps,
            )
        except Exception as e:
            logger.error("Log snapshot failed: %s", e)

    async def run(self):
        logger.info("Starting collection loop (interval: %ss)", self.interval)
        last_log = datetime.now(timezone.utc) - timedelta(seconds=self.interval)

        while True:
            try:
                now = datetime.now(timezone.utc)
                if (now - last_log).total_seconds() >= self.interval:
                    bids, asks = self._fetch_order_book_safe()
                    self._log_snapshot(bids, asks)
                    last_log = now

                for _ in range(10):
                    await asyncio.sleep(1)
            except KeyboardInterrupt:
                logger.info("Collector stopped by user (Ctrl+C)")
                break
            except Exception as e:
                logger.error("Loop error: %s. Retrying in 15s...", e)
                await asyncio.sleep(15)

        close_fn = getattr(self.exchange, "close", None)
        if close_fn:
            try:
                res = close_fn()
                if asyncio.iscoroutine(res):
                    await res
            except Exception:
                pass
        logger.info("Collector shutdown complete")


def main():
    parser = argparse.ArgumentParser(description="DOM/CVD Collector for Quantrex (standalone)")
    parser.add_argument("--symbol", default="BTC/USDT", help="Trading pair")
    parser.add_argument("--depth", type=int, default=20, help="Order book depth")
    parser.add_argument("--interval", type=int, default=900, help="Log interval seconds")
    args = parser.parse_args()

    collector = DOMCVDCollector(
        symbol=args.symbol,
        depth=args.depth,
        interval_sec=args.interval,
    )
    try:
        asyncio.run(collector.run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
