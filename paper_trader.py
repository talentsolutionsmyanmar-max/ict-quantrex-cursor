import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pandas as pd

from config import Config
from data_handler import DataHandler
from ict_engine import ICTEngine
from ict_execution import (
    atr_at_index,
    check_sl_hit,
    check_tp_hit,
    close_partial_pnl,
    compute_sl_tp,
    confluence_breakdown,
    format_confluence_pretty,
    trail_stop_price,
    unrealized_pnl,
)
from playbook_reason import entry_context_json_str, entry_snapshot, exit_narrative
from trade_playbook import record_playbook_event


def _paper_symbols(config: Config) -> List[str]:
    wl = getattr(config, "WATCHLIST", None)
    if not wl:
        wl = []
    out = [str(s).upper().replace("/", "") for s in wl if s]
    if out:
        return out
    return [str(config.SYMBOL).upper().replace("/", "")]


@dataclass
class SymbolPaperBook:
    symbol: str
    capital: float
    position: int = 0
    entry_price: float = 0.0
    entry_time: Optional[datetime] = None
    stop_loss: float = 0.0
    stop_at_entry: float = 0.0
    tp1: float = 0.0
    tp2: float = 0.0
    tp3: float = 0.0
    position_remaining: float = 1.0
    atr_at_entry: float = 0.0
    bars_held: int = 0
    last_bar_ts: Any = None
    trades: list = field(default_factory=list)
    entry_reason_json: Optional[str] = None
    entry_reason_text: Optional[str] = None


class PaperTrader:
    """
    Paper loop: same ICT entry/exit rules as backtester.
    Multiple symbols: capital split evenly across WATCHLIST (from spec); each symbol has its own book.
    """

    def __init__(self, config: Config, socketio):
        self.config = config
        self.socketio = socketio
        self.data_handler = DataHandler(config)
        self.ict = ICTEngine(config)
        self.running = False
        self.symbols = _paper_symbols(config)
        n = max(1, len(self.symbols))
        slice_cap = float(config.INITIAL_CAPITAL) / n
        self.books: Dict[str, SymbolPaperBook] = {s: SymbolPaperBook(symbol=s, capital=slice_cap) for s in self.symbols}
        self.trades: list = []

    def run(self):
        self.running = True
        print(
            f"📄 Paper trading — {len(self.symbols)} symbol(s): {', '.join(self.symbols)} "
            f"(~${self.books[self.symbols[0]].capital:,.0f} each)"
        )

        while self.running:
            try:
                per_payload: Dict[str, Any] = {}
                for sym, book in self.books.items():
                    df = self.data_handler.fetch_live_data(limit=200, symbol=sym)
                    df = self.ict.process_dataframe(df)
                    idx = len(df) - 1
                    row = df.iloc[idx]
                    bar_ts = row["timestamp"]

                    if book.last_bar_ts is not None and book.position != 0 and bar_ts != book.last_bar_ts:
                        book.bars_held += 1
                    book.last_bar_ts = bar_ts

                    self._process_bar(book, df, idx, row, bar_ts)
                    per_payload[sym] = self._live_payload(book, row, float(row["close"]))

                self.socketio.emit("live_data", {"per_symbol": per_payload, "watchlist": list(self.symbols)})

                time.sleep(float(self.config.POLL_INTERVAL_SEC))
            except Exception as e:
                print(f"Paper trading error: {e}")
                time.sleep(5)

    def _bar_ts_str(self, row: pd.Series) -> str:
        t = row.name
        return t.isoformat() if hasattr(t, "isoformat") else str(t)

    def _live_payload(self, book: SymbolPaperBook, row: pd.Series, price: float) -> Dict[str, Any]:
        payload = {
            "symbol": book.symbol,
            "timestamp": self._bar_ts_str(row),
            "price": price,
            "signal": int(row["signal"]),
            "signal_strength": float(row["signal_strength"]),
            "premium": bool(row.get("premium", False)),
            "discount": bool(row.get("discount", False)),
            "capital": float(book.capital),
            "position": int(book.position),
            "confluence": int(confluence_breakdown(row, self.config)["count"]),
            "bars_held": book.bars_held,
            "position_remaining": float(book.position_remaining),
            "unrealized_pnl": 0.0,
            "entry_price": None,
            "stop_loss": None,
            "tp1": None,
            "tp2": None,
            "tp3": None,
            "risk_r": None,
        }
        if book.position != 0:
            u = unrealized_pnl(
                book.position,
                book.entry_price,
                price,
                book.position_remaining,
                book.capital,
                book.stop_at_entry,
                self.config,
            )
            payload["unrealized_pnl"] = float(u)
            payload["entry_price"] = float(book.entry_price)
            payload["stop_loss"] = float(book.stop_loss)
            payload["tp1"] = float(book.tp1)
            payload["tp2"] = float(book.tp2)
            payload["tp3"] = float(book.tp3)
            rd = abs(book.entry_price - book.stop_at_entry)
            payload["risk_r"] = float(abs(price - book.entry_price) / rd) if rd > 0 else 0.0
        return payload

    def _process_bar(self, book: SymbolPaperBook, df: pd.DataFrame, idx: int, row: pd.Series, bar_ts: Any):
        price = float(row["close"])

        if book.position != 0 and book.bars_held >= int(self.config.MAX_CANDLES_HOLD):
            self._close_all(book, float(row["close"]), row, "TIME_EXIT")

        if book.position != 0 and book.position_remaining == 1.0 and check_tp_hit(book.position, row, book.tp1):
            self._partial_exit(book, float(row["close"]), row, self.config.TP1_PCT, "TP1")

        if book.position != 0 and book.position_remaining > 0 and book.position_remaining <= 0.50 and check_tp_hit(
            book.position, row, book.tp2
        ):
            self._partial_exit(book, float(row["close"]), row, self.config.TP2_PCT, "TP2")

        if book.position != 0 and book.position_remaining > 0 and book.position_remaining <= 0.20 and check_tp_hit(
            book.position, row, book.tp3
        ):
            self._partial_exit(book, float(row["close"]), row, self.config.TP3_PCT, "TP3")
            if book.position_remaining <= 1e-9:
                self._clear_position(book)

        if book.position != 0 and book.position_remaining > 0 and check_sl_hit(book.position, row, book.stop_loss):
            self._partial_exit(book, float(row["close"]), row, book.position_remaining, "STOP_LOSS")
            self._clear_position(book)

        if (
            book.position != 0
            and book.position_remaining < 1.0
            and book.position_remaining > 0
            and bool(self.config.TRAIL_AFTER_TP1)
        ):
            trail = trail_stop_price(book.position, book.entry_price, book.atr_at_entry, row, self.config)
            if check_sl_hit(book.position, row, trail):
                self._partial_exit(book, float(row["close"]), row, book.position_remaining, "TRAIL_STOP")
                self._clear_position(book)

        if (
            book.position != 0
            and int(row.get("signal", 0)) != 0
            and int(row.get("signal", 0)) != book.position
            and float(row.get("signal_strength", 0)) >= float(self.config.MIN_SIGNAL_STRENGTH)
        ):
            self._partial_exit(book, float(row["close"]), row, book.position_remaining, "SIGNAL_REVERSAL")
            self._clear_position(book)

        if book.position != 0 and book.position_remaining <= 1e-6:
            self._clear_position(book)

        if book.position == 0 and int(row.get("signal", 0)) != 0:
            cx = confluence_breakdown(row, self.config)
            c = int(cx["count"])
            if c >= int(self.config.MIN_CONFLUENCE) and float(row.get("signal_strength", 0)) >= float(
                self.config.MIN_SIGNAL_STRENGTH
            ):
                book.position = int(row["signal"])
                book.entry_price = price
                book.entry_time = datetime.now(timezone.utc)
                book.position_remaining = 1.0
                book.bars_held = 0
                book.last_bar_ts = bar_ts

                book.atr_at_entry = float(atr_at_index(df, idx, period=14))
                levels = compute_sl_tp(book.position, book.entry_price, row, book.atr_at_entry, self.config)
                book.stop_loss = levels["stop_loss"]
                book.stop_at_entry = book.stop_loss
                book.tp1 = levels["tp1"]
                book.tp2 = levels["tp2"]
                book.tp3 = levels["tp3"]

                ctx, txt = entry_snapshot(
                    row,
                    c,
                    book.symbol,
                    self.config,
                    confluence_reasons=cx.get("reasons"),
                    confluence_flags=cx.get("flags"),
                    confluence_thresholds=cx.get("thresholds"),
                )
                book.entry_reason_json = entry_context_json_str(ctx)
                book.entry_reason_text = txt

                # Readable audit log (paper only; no performance impact).
                try:
                    pretty = format_confluence_pretty(cx, min_confluence_required=int(self.config.MIN_CONFLUENCE))
                    print(f"Entry taken | {book.symbol} | {('LONG' if book.position==1 else 'SHORT')} | {pretty}")
                except Exception:
                    pass

                record_playbook_event(
                    mode="PAPER",
                    symbol=book.symbol,
                    timeframe=self.config.TIMEFRAME,
                    event_type="OPEN",
                    side="LONG" if book.position == 1 else "SHORT",
                    entry_price=book.entry_price,
                    exit_price=None,
                    position_fraction=1.0,
                    pnl=None,
                    capital_after=book.capital,
                    bar_time=self._bar_ts_str(row),
                    entry_reason_json=book.entry_reason_json,
                    entry_reason_text=book.entry_reason_text,
                    exit_reason_text=None,
                )

                self.socketio.emit(
                    "trade_executed",
                    {
                        "type": "OPEN",
                        "symbol": book.symbol,
                        "side": "LONG" if book.position == 1 else "SHORT",
                        "price": book.entry_price,
                        "capital": float(book.capital),
                        "stop_loss": book.stop_loss,
                        "tp1": book.tp1,
                        "tp2": book.tp2,
                        "tp3": book.tp3,
                        "confluence": c,
                        "entry_reason_text": book.entry_reason_text,
                    },
                )

    def _partial_exit(self, book: SymbolPaperBook, exit_price: float, row: pd.Series, fraction: float, exit_type: str):
        pnl = close_partial_pnl(
            book.position,
            exit_price,
            book.entry_price,
            fraction,
            book.capital,
            book.stop_at_entry,
            self.config,
        )
        book.capital += pnl
        book.position_remaining = max(0.0, book.position_remaining - float(fraction))
        book.trades.append(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "symbol": book.symbol,
                "exit_type": exit_type,
                "pnl": pnl,
                "exit_price": exit_price,
            }
        )
        self.trades.append(book.trades[-1])

        ext = exit_narrative(
            exit_type,
            row,
            entry_reason_text=book.entry_reason_text,
            exit_price=exit_price,
            bars_held=book.bars_held,
            reversal_min_strength=float(getattr(self.config, "MIN_SIGNAL_STRENGTH", 70)),
        )
        record_playbook_event(
            mode="PAPER",
            symbol=book.symbol,
            timeframe=self.config.TIMEFRAME,
            event_type=exit_type,
            side="LONG" if book.position == 1 else "SHORT",
            entry_price=book.entry_price,
            exit_price=exit_price,
            position_fraction=float(fraction),
            pnl=float(pnl),
            capital_after=book.capital,
            bar_time=self._bar_ts_str(row),
            entry_reason_json=book.entry_reason_json,
            entry_reason_text=book.entry_reason_text,
            exit_reason_text=ext,
        )

        self.socketio.emit(
            "trade_executed",
            {
                "type": exit_type,
                "symbol": book.symbol,
                "pnl": pnl,
                "capital": float(book.capital),
                "exit_price": exit_price,
                "position_remaining": float(book.position_remaining),
                "exit_reason_text": ext,
            },
        )

    def _close_all(self, book: SymbolPaperBook, exit_price: float, row: pd.Series, exit_type: str):
        if book.position_remaining <= 0:
            self._clear_position(book)
            return
        self._partial_exit(book, exit_price, row, book.position_remaining, exit_type)
        self._clear_position(book)

    def _clear_position(self, book: SymbolPaperBook):
        book.position = 0
        book.entry_price = 0.0
        book.entry_time = None
        book.stop_loss = 0.0
        book.stop_at_entry = 0.0
        book.tp1 = book.tp2 = book.tp3 = 0.0
        book.position_remaining = 1.0
        book.atr_at_entry = 0.0
        book.bars_held = 0
        book.entry_reason_json = None
        book.entry_reason_text = None

    def stop(self):
        self.running = False
        for book in self.books.values():
            if book.position != 0 and book.position_remaining > 0:
                try:
                    df = self.data_handler.fetch_live_data(limit=20, symbol=book.symbol)
                    df = self.ict.process_dataframe(df)
                    row = df.iloc[-1]
                    self._partial_exit(book, float(row["close"]), row, book.position_remaining, "STOP_SESSION")
                except Exception as e:
                    print(f"Flatten on stop ({book.symbol}): {e}")
            self._clear_position(book)
        print("📄 Paper trading stopped")
