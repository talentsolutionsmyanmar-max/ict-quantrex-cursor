import argparse
import os
import time
import json
import atexit
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from config import Config, build_config
from data_handler import DataHandler
from execution.dirty_fill_simulator import DirtyFillSimulator, FillResult, neutral_fill
from ict_engine import ICTEngine, _wilder_atr_series
from ict_execution import (
    atr_at_index,
    check_sl_hit,
    check_tp_hit,
    close_partial_pnl,
    compute_sl_tp,
    confluence_breakdown,
    format_confluence_pretty,
    position_size_risk,
    regime_risk_overrides,
    trail_stop_price,
    unrealized_pnl,
)
from playbook_reason import entry_context_json_str, entry_snapshot, exit_narrative
from orderbook_micro import binance_depth_micro_snapshot
from binance_public_enrich import binance_free_entry_enrichment
from mmt_client import fetch_stats_entry_enrichment, fetch_vd_entry_enrichment, mmt_configured
from trade_playbook import record_playbook_event
from paper_alerts import notify_paper_exit, notify_paper_open
from risk_engine import RiskEngine
from strategy.load_spec import read_raw_spec
from monitoring.signal_audit import (
    append_signal_audit_jsonl,
    log_signal_decision,
    merge_pattern_flags,
    pattern_flags_from_row,
)
from monitoring.supabase_rest_logger import log_trade_to_supabase
from research.meta_hypothesis_logger import generate_auto_hypothesis, log_trade_postmortem
from core.scoring_hook import HybridScoringHook
from core.signal_generator import should_open_position
from core.exit_engine import apply_regime_exit_logic, calculate_r_multiple
from core.live_market_feed import LiveMarketFeed
from core.regime_detector_live import detect_regime_live

logger = logging.getLogger("quantrex.pid")
PID_FILE = Path("data/quantrex.pid")


def manage_pid() -> None:
    """Write current PID and register cleanup for process exit."""
    pid = os.getpid()
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(pid), encoding="utf-8")
    logger.info("PID %s registered. System active.", pid)

    def remove_pid() -> None:
        try:
            if PID_FILE.exists() and PID_FILE.read_text(encoding="utf-8").strip() == str(pid):
                PID_FILE.unlink()
        except Exception:
            pass

    atexit.register(remove_pid)


def _max_hold_bars_from_hours(hours: float, timeframe: str) -> int:
    s = str(timeframe or "15m").strip().lower()
    n = 15
    if s.endswith("m") and s[:-1].isdigit():
        n = int(s[:-1])
    return max(1, int(float(hours) * 60.0 / max(n, 1)))


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
    tp1: Optional[float] = None
    tp2: float = 0.0
    tp3: float = 0.0
    position_remaining: float = 1.0
    atr_at_entry: float = 0.0
    bars_held: int = 0
    entry_qty: float = 0.0
    last_bar_ts: Any = None
    trades: list = field(default_factory=list)
    entry_reason_json: Optional[str] = None
    entry_reason_text: Optional[str] = None
    last_fill: Optional[FillResult] = None
    last_confluence_at_entry: float = 0.0
    high_since_entry: float = 0.0
    low_since_entry: float = 0.0
    max_hold_bars: int = 0
    entry_regime_state: str = "unknown"


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
        alloc = self._initial_capital_allocation()
        self.books: Dict[str, SymbolPaperBook] = {
            s: SymbolPaperBook(symbol=s, capital=float(alloc.get(s, 0.0))) for s in self.symbols
        }
        self.trades: list = []
        self.risk = RiskEngine(config)

    def _apply_regime_dynamic_stop(self, book: SymbolPaperBook, row: pd.Series) -> None:
        if book.position == 0 or book.entry_qty <= 0:
            return
        raw = read_raw_spec()
        exits_cfg = raw.get("exits") if isinstance(raw, dict) else {}
        regime = str(book.entry_regime_state or "unknown")
        if not isinstance(exits_cfg, dict) or not exits_cfg.get(regime):
            return
        stop_distance = abs(float(book.entry_price) - float(book.stop_at_entry))
        if stop_distance <= 0:
            return
        unrealized_r = 0.0
        if book.position == 1:
            unrealized_r = (float(row["close"]) - float(book.entry_price)) / stop_distance
        else:
            unrealized_r = (float(book.entry_price) - float(row["close"])) / stop_distance
        trade_state = {
            "entry_price": float(book.entry_price),
            "stop_price": float(book.stop_loss),
            "stop_distance": float(stop_distance),
            "direction": "long" if book.position == 1 else "short",
            "unrealized_r": float(unrealized_r),
            "high_since_entry": float(book.high_since_entry),
            "low_since_entry": float(book.low_since_entry),
        }
        apply_regime_exit_logic(trade_state, exits_cfg, regime)
        book.stop_loss = float(trade_state.get("stop_price", book.stop_loss))
        if os.getenv("PAPER_EXIT_DEBUG", "").strip().lower() in ("1", "true", "yes", "on"):
            print(
                f"PAPER_EXIT_DEBUG | {book.symbol} | regime={regime} | uR={unrealized_r:.3f} | "
                f"stop={book.stop_loss:.6f}"
            )

    def _initial_capital_allocation(self) -> Dict[str, float]:
        total = float(self.config.INITIAL_CAPITAL)
        n = max(1, len(self.symbols))
        equal = {s: total / n for s in self.symbols}
        method = str(getattr(self.config, "ALLOCATION_METHOD", "equal") or "equal").lower()
        if method != "volatility_parity" or n <= 1:
            return equal
        vols: Dict[str, float] = {}
        for s in self.symbols:
            try:
                d = self.data_handler.fetch_live_data(limit=220, symbol=s)
                rets = pd.to_numeric(d["close"], errors="coerce").pct_change().dropna()
                v = float(rets.std()) if len(rets) > 20 else 0.0
                vols[s] = max(v, 1e-6)
            except Exception:
                vols[s] = 1e-6
        inv = {s: 1.0 / vols[s] for s in self.symbols}
        z = sum(inv.values())
        if z <= 0:
            return equal
        return {s: total * (inv[s] / z) for s in self.symbols}

    def _dirty_execution_enabled(self) -> bool:
        raw = read_raw_spec()
        de = raw.get("dirty_execution") or {}
        return bool(de.get("enabled")) and str(getattr(self.config, "MODE", "")).upper() == "PAPER"

    def run(self):
        self.running = True
        print(
            f"Paper trading — {len(self.symbols)} symbol(s): {', '.join(self.symbols)} "
            f"(allocation={str(getattr(self.config, 'ALLOCATION_METHOD', 'equal'))})"
        )
        print("Regime dynamic stops: set PAPER_EXIT_DEBUG=1 for per-bar stop logs (trend_down parity with backtest).")

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

    def _open_positions_count(self) -> int:
        return sum(1 for b in self.books.values() if int(b.position) != 0)

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
                book.entry_qty,
                self.config,
            )
            payload["unrealized_pnl"] = float(u)
            payload["entry_price"] = float(book.entry_price)
            payload["stop_loss"] = float(book.stop_loss)
            payload["tp1"] = float(book.tp1) if book.tp1 is not None else None
            payload["tp2"] = float(book.tp2)
            payload["tp3"] = float(book.tp3)
            rd = abs(book.entry_price - book.stop_at_entry)
            payload["risk_r"] = float(abs(price - book.entry_price) / rd) if rd > 0 else 0.0
        return payload

    def _process_bar(self, book: SymbolPaperBook, df: pd.DataFrame, idx: int, row: pd.Series, bar_ts: Any):
        price = float(row["close"])

        if book.position != 0:
            book.high_since_entry = max(book.high_since_entry, float(row["high"]))
            book.low_since_entry = min(book.low_since_entry, float(row["low"]))
            self._apply_regime_dynamic_stop(book, row)

        max_hold = int(book.max_hold_bars or self.config.MAX_CANDLES_HOLD)
        if book.position != 0 and book.bars_held >= max_hold:
            self._close_all(book, float(row["close"]), row, "TIME_EXIT")

        if book.position != 0 and book.position_remaining == 1.0 and book.tp1 is not None and check_tp_hit(
            book.position, row, float(book.tp1)
        ):
            self._partial_exit(book, float(book.tp1), row, self.config.TP1_PCT, "TP1")

        if book.position != 0 and book.position_remaining > 0 and book.position_remaining <= 0.50 and check_tp_hit(
            book.position, row, book.tp2
        ):
            self._partial_exit(book, float(book.tp2), row, self.config.TP2_PCT, "TP2")

        if book.position != 0 and book.position_remaining > 0 and book.position_remaining <= 0.20 and check_tp_hit(
            book.position, row, book.tp3
        ):
            self._partial_exit(book, float(book.tp3), row, self.config.TP3_PCT, "TP3")
            if book.position_remaining <= 1e-9:
                self._clear_position(book)

        if book.position != 0 and book.position_remaining > 0 and check_sl_hit(book.position, row, book.stop_loss):
            sl_reason = self._sl_reason_if_enabled(book, df, row)
            self._partial_exit(book, float(book.stop_loss), row, book.position_remaining, "STOP_LOSS", sl_reason=sl_reason)
            self._clear_position(book)

        if (
            book.position != 0
            and book.position_remaining < 1.0
            and book.position_remaining > 0
            and bool(self.config.TRAIL_AFTER_TP1)
        ):
            trail = trail_stop_price(book.position, book.entry_price, book.atr_at_entry, row, self.config)
            if check_sl_hit(book.position, row, trail):
                self._partial_exit(book, float(trail), row, book.position_remaining, "TRAIL_STOP")
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
            raw = read_raw_spec()
            universe_cfg = raw.get("trading_universe") if isinstance(raw, dict) else {}
            regime_ok, regime_msg = should_open_position(str(row.get("regime_state") or "unknown"), universe_cfg)
            if not regime_ok:
                try:
                    log_signal_decision(
                        {
                            "ts": datetime.now(timezone.utc).isoformat(),
                            "symbol": book.symbol,
                            "regime": row.get("regime_state"),
                            "strength": float(row.get("signal_strength", 0) or 0.0),
                            "confluence": 0,
                            "decision": "SKIP",
                            "skip_reason": regime_msg,
                        },
                        row=row,
                    )
                except Exception:
                    pass
                return
            cap = getattr(self.config, "MAX_CONCURRENT_POSITIONS", None)
            if cap is not None and int(cap) > 0 and self._open_positions_count() >= int(cap):
                try:
                    log_signal_decision(
                        {
                            "ts": datetime.now(timezone.utc).isoformat(),
                            "symbol": book.symbol,
                            "regime": row.get("regime_state"),
                            "strength": float(row.get("signal_strength", 0) or 0.0),
                            "confluence": 0,
                            "decision": "SKIP",
                            "skip_reason": "max_concurrent_positions",
                        },
                        row=row,
                    )
                except Exception:
                    pass
                return
            cx = confluence_breakdown(row, self.config)
            c = int(cx["count"])
            if c >= int(self.config.MIN_CONFLUENCE) and float(row.get("signal_strength", 0)) >= float(
                self.config.MIN_SIGNAL_STRENGTH
            ):
                gate_ok, gate_msgs = self.risk.check_entry_gates(book.symbol)
                if not gate_ok:
                    print(f"Entry blocked | {book.symbol} | {'; '.join(gate_msgs)}")
                    try:
                        log_signal_decision(
                            {
                                "ts": datetime.now(timezone.utc).isoformat(),
                                "symbol": book.symbol,
                                "regime": row.get("regime_state"),
                                "strength": float(row.get("signal_strength", 0) or 0.0),
                                "confluence": c,
                                "fvg": bool(cx.get("flags", {}).get("fvg")) if isinstance(cx.get("flags"), dict) else False,
                                "corr_ok": False,
                                "decision": "SKIP",
                                "skip_reason": "; ".join(gate_msgs),
                            },
                            row=row,
                        )
                    except Exception:
                        pass
                    return
                try:
                    log_signal_decision(
                        {
                            "ts": datetime.now(timezone.utc).isoformat(),
                            "symbol": book.symbol,
                            "regime": row.get("regime_state"),
                            "strength": float(row.get("signal_strength", 0) or 0.0),
                            "confluence": c,
                            "fvg": bool(cx.get("flags", {}).get("fvg")) if isinstance(cx.get("flags"), dict) else False,
                            "corr_ok": True,
                            "decision": "ENTER",
                        }
                    )
                except Exception:
                    pass
                book.position = int(row["signal"])
                book.last_confluence_at_entry = float(row.get("signal_strength", 0) or 0.0)
                book.atr_at_entry = float(atr_at_index(df, idx, period=14))
                atr_mult_eff, size_mult_eff = regime_risk_overrides(row, self.config, raw)
                pre_levels = compute_sl_tp(
                    book.position, price, row, book.atr_at_entry, self.config, atr_multiplier_override=atr_mult_eff
                )
                req_qty = position_size_risk(
                    capital=book.capital,
                    entry=price,
                    stop_price=float(pre_levels["stop_loss"]),
                    config=self.config,
                    atr=book.atr_at_entry,
                    size_multiplier=size_mult_eff,
                )
                use_dirty = self._dirty_execution_enabled() and "volume" in df.columns
                if use_dirty:
                    atr_s = _wilder_atr_series(df, period=14)
                    vol_s = pd.to_numeric(df["volume"], errors="coerce").fillna(0.0)
                    sim = DirtyFillSimulator(raw, atr_s, vol_s)
                    direction = "buy" if book.position == 1 else "sell"
                    fill = sim.simulate_fill(
                        row_index=idx,
                        requested_price=price,
                        requested_qty=max(1e-9, req_qty),
                        direction=direction,
                        symbol=book.symbol,
                    )
                    book.last_fill = fill
                    book.entry_price = float(fill.filled_price)
                    time.sleep(min(2.5, max(0.0, fill.latency_ms / 1000.0)))
                    if bool(getattr(self.config, "MODEL_PARTIAL_FILLS", True)) and fill.partial_fill:
                        book.position_remaining = min(1.0, max(1e-9, fill.filled_qty / max(req_qty, 1e-9)))
                        book.entry_qty = float(fill.filled_qty)
                    else:
                        book.position_remaining = 1.0
                        book.entry_qty = float(req_qty)
                    if bool(getattr(self.config, "LOG_EVERY_FILL", False)):
                        print(
                            f"Dirty fill | {book.symbol} | req={fill.requested_price:.6f} "
                            f"fill={fill.filled_price:.6f} bps={fill.slippage_bps:.1f} "
                            f"lat_ms={fill.latency_ms} partial={fill.partial_fill}"
                        )
                else:
                    book.last_fill = neutral_fill(price, req_qty)
                    book.entry_price = price
                    book.position_remaining = 1.0
                    book.entry_qty = float(req_qty)

                book.entry_time = datetime.now(timezone.utc)
                book.bars_held = 0
                book.last_bar_ts = bar_ts

                levels = compute_sl_tp(
                    book.position,
                    book.entry_price,
                    row,
                    book.atr_at_entry,
                    self.config,
                    atr_multiplier_override=atr_mult_eff,
                )
                book.stop_loss = levels["stop_loss"]
                book.stop_at_entry = book.stop_loss
                book.tp1 = levels["tp1"]
                book.tp2 = levels["tp2"]
                book.tp3 = levels["tp3"]
                rs = row.get("regime_state")
                book.entry_regime_state = "unknown" if pd.isna(rs) else str(rs)
                exits_cfg = raw.get("exits") if isinstance(raw, dict) else {}
                if isinstance(exits_cfg, dict):
                    rc = dict(exits_cfg.get(book.entry_regime_state, {}) or {})
                    risk_dist = abs(float(book.entry_price) - float(book.stop_at_entry))
                    tp1r = rc.get("tp1_ratio", self.config.TP1_RATIO)
                    if tp1r is None:
                        book.tp1 = None
                    else:
                        book.tp1 = (
                            float(book.entry_price + risk_dist * float(tp1r))
                            if book.position == 1
                            else float(book.entry_price - risk_dist * float(tp1r))
                        )
                    if rc.get("tp2_ratio") is not None:
                        book.tp2 = (
                            float(book.entry_price + risk_dist * float(rc["tp2_ratio"]))
                            if book.position == 1
                            else float(book.entry_price - risk_dist * float(rc["tp2_ratio"]))
                        )
                    if rc.get("tp3_ratio") is not None:
                        book.tp3 = (
                            float(book.entry_price + risk_dist * float(rc["tp3_ratio"]))
                            if book.position == 1
                            else float(book.entry_price - risk_dist * float(rc["tp3_ratio"]))
                        )
                    mh = rc.get("max_holding_hours")
                    if mh is not None:
                        book.max_hold_bars = _max_hold_bars_from_hours(float(mh), str(self.config.TIMEFRAME))
                    else:
                        book.max_hold_bars = 0
                book.high_since_entry = float(row["high"])
                book.low_since_entry = float(row["low"])

                ctx, txt = entry_snapshot(
                    row,
                    c,
                    book.symbol,
                    self.config,
                    confluence_reasons=cx.get("reasons"),
                    confluence_flags=cx.get("flags"),
                    confluence_thresholds=cx.get("thresholds"),
                )
                pt = raw.get("pre_trade") if isinstance(raw, dict) else {}
                if isinstance(pt, dict) and pt.get("enabled"):
                    ctx_m = dict(ctx)
                    ctx_m["pre_trade"] = {k: v for k, v in pt.items() if k not in ("enabled",)}
                    if pt.get("log_binance_depth_snapshot"):
                        ctx_m["microstructure"] = binance_depth_micro_snapshot(
                            symbol=book.symbol,
                            base_url=str(self.config.BINANCE_API),
                            limit=int(pt.get("depth_levels") or 50),
                            top_n=int(pt.get("depth_top_n") or 20),
                        )
                    bpe = pt.get("binance_public_enrich")
                    if isinstance(bpe, dict) and bpe.get("enabled"):
                        inc = bpe.get("include")
                        ctx_m["binance_public_entry"] = binance_free_entry_enrichment(
                            symbol=book.symbol,
                            spot_base_url=str(self.config.BINANCE_API),
                            futures_api_v1_url=str(
                                getattr(self.config, "BINANCE_FUTURES_API", "") or "https://fapi.binance.com/fapi/v1"
                            ),
                            agg_trades_limit=int(bpe.get("agg_trades_limit", 400)),
                            timeout_sec=float(bpe.get("timeout_sec", 1.5)),
                            oi_period=str(bpe.get("oi_period", "5m")),
                            oi_hist_limit=int(bpe.get("oi_hist_limit", 3)),
                            include=list(inc) if isinstance(inc, list) else None,
                        )
                    mme = pt.get("mmt_enrich")
                    if isinstance(mme, dict) and mme.get("enabled") and mmt_configured():
                        inc = mme.get("include")
                        tmo = float(mme.get("timeout_sec", 0.8))
                        tf_m = str(mme.get("tf") or "1m")
                        lb_m = int(mme.get("lookback_sec", 420))
                        ex_m = str(mme.get("exchange") or "binancef")
                        ctx_m["mmt_stats_entry"] = fetch_stats_entry_enrichment(
                            exchange=ex_m,
                            venue_symbol=book.symbol,
                            timeout_sec=tmo,
                            tf=tf_m,
                            lookback_sec=lb_m,
                            include=list(inc) if isinstance(inc, list) else None,
                        )
                        if mme.get("vd_enabled"):
                            ctx_m["mmt_vd_entry"] = fetch_vd_entry_enrichment(
                                exchange=ex_m,
                                venue_symbol=book.symbol,
                                timeout_sec=tmo,
                                tf=tf_m,
                                lookback_sec=lb_m,
                                bucket=int(mme.get("vd_bucket", 1) or 1),
                            )
                    book.entry_reason_json = entry_context_json_str(ctx_m)
                else:
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
            else:
                try:
                    log_signal_decision(
                        {
                            "ts": datetime.now(timezone.utc).isoformat(),
                            "symbol": book.symbol,
                            "regime": row.get("regime_state"),
                            "strength": float(row.get("signal_strength", 0) or 0.0),
                            "confluence": c,
                            "fvg": bool(cx.get("flags", {}).get("fvg")) if isinstance(cx.get("flags"), dict) else False,
                            "decision": "SKIP",
                            "skip_reason": "confluence_or_strength",
                        },
                        row=row,
                    )
                except Exception:
                    pass
                notify_paper_open(
                    book.symbol,
                    "LONG" if book.position == 1 else "SHORT",
                    float(book.entry_price),
                    float(book.stop_loss),
                    c,
                    book.entry_reason_text,
                )

    def _sl_reason_if_enabled(self, book: SymbolPaperBook, df: pd.DataFrame, row: pd.Series) -> Optional[str]:
        raw = read_raw_spec()
        de = raw.get("dirty_execution") or {}
        if not de.get("log_sl_reason"):
            return None
        fill = book.last_fill if book.last_fill is not None else neutral_fill(book.entry_price, book.position_remaining)
        atr_s = _wilder_atr_series(df, period=14)
        vol_s = pd.to_numeric(df["volume"], errors="coerce").fillna(0.0) if "volume" in df.columns else pd.Series(
            1.0, index=df.index
        )
        sim = DirtyFillSimulator(raw, atr_s, vol_s)
        return sim.tag_sl_reason(
            fill,
            row,
            float(book.stop_loss),
            float(book.last_confluence_at_entry or 0.0),
            float(book.atr_at_entry or 0.0),
        )

    def _partial_exit(
        self,
        book: SymbolPaperBook,
        exit_price: float,
        row: pd.Series,
        fraction: float,
        exit_type: str,
        sl_reason: Optional[str] = None,
    ):
        pnl = close_partial_pnl(
            book.position,
            exit_price,
            book.entry_price,
            fraction,
            book.entry_qty,
            self.config,
        )
        book.capital += pnl
        book.position_remaining = max(0.0, book.position_remaining - float(fraction))
        trade_rec: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "symbol": book.symbol,
            "exit_type": exit_type,
            "pnl": pnl,
            "exit_price": exit_price,
            "entry_price": float(book.entry_price),
            "regime": str(book.entry_regime_state or "unknown"),
        }
        if sl_reason:
            trade_rec["sl_reason"] = sl_reason
        book.trades.append(trade_rec)
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
        notify_paper_exit(book.symbol, exit_type, float(pnl), float(exit_price), ext)
        stop_ratio = (
            abs(float(book.entry_price) - float(book.stop_at_entry)) / float(book.entry_price)
            if float(book.entry_price) > 0
            else 0.0
        )
        direction = "long" if int(book.position) == 1 else "short"
        r_multiple = calculate_r_multiple(
            entry_price=float(book.entry_price),
            exit_price=float(exit_price),
            stop_distance=float(stop_ratio),
            direction=direction,
        )
        try:
            trade_ctx = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "symbol": book.symbol,
                "regime": str(row.get("regime_state") or "unknown"),
                "entry_reason": str(book.entry_reason_text or ""),
                "exit_reason": str(ext or ""),
                "r_multiple": r_multiple,
                "pnl": float(pnl),
            }
            hyp = generate_auto_hypothesis(trade_ctx)
            log_trade_postmortem(
                trade_ctx,
                explanation=f"Auto postmortem | exit={exit_type} | pnl={float(pnl):.4f} | r={r_multiple:.4f}",
                hypothesis_id=str(hyp.get("id")),
            )
        except Exception:
            pass
        try:
            log_trade_to_supabase(
                {
                    "trade_id": f"{book.symbol}-{trade_rec['timestamp']}",
                    "symbol": book.symbol,
                    "regime": str(book.entry_regime_state or "unknown"),
                    "entry_price": float(book.entry_price),
                    "exit_price": float(exit_price),
                    "r_multiple": float(r_multiple),
                    "exit_reason": str(exit_type),
                    "pnl_usd": float(pnl),
                    "timestamp": trade_rec["timestamp"],
                    "paper_mode": True,
                }
            )
        except Exception:
            pass

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
        book.tp1 = None
        book.tp2 = book.tp3 = 0.0
        book.position_remaining = 1.0
        book.atr_at_entry = 0.0
        book.entry_qty = 0.0
        book.bars_held = 0
        book.entry_reason_json = None
        book.entry_reason_text = None
        book.last_fill = None
        book.last_confluence_at_entry = 0.0

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
        print("Paper trading stopped")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="", help="Path to YAML spec (sets STRATEGY_SPEC_PATH)")
    parser.add_argument("--dry-run", action="store_true", help="Run backtester and print first 3 trades")
    parser.add_argument("--live", action="store_true", help="Run live paper loop from Binance spot feed")
    parser.add_argument("--symbol", default="BTC/USDT", help="Exchange symbol for --live mode")
    parser.add_argument("--live-seconds", type=int, default=0, help="Auto-stop live loop after N seconds")
    parser.add_argument("--test-pipeline", action="store_true", help="Force one mock entry/exit log event")
    parser.add_argument("--debug-signals", action="store_true", help="Print confluence/strength/skip reasons each candle")
    parser.add_argument("--mode", default="PAPER", help="Runtime mode override")
    args = parser.parse_args()

    if args.config:
        os.environ["STRATEGY_SPEC_PATH"] = str(args.config)

    cfg = build_config()
    cfg.MODE = str(args.mode or "PAPER").upper()

    if args.test_pipeline:
        mock_trade = {
            "id": "test_01",
            "symbol": "BTCUSDT",
            "regime": "trend_down",
            "side": "SHORT",
            "entry_price": 77000,
            "exit_price": 76800,
            "r_multiple": 0.85,
            "exit_reason": "TEST_RUN",
            "pnl_usd": 200,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "paper_mode": True,
        }
        out = log_trade_to_supabase(mock_trade)
        print(f"test_pipeline_result={out}")
        print("Test trade logged. Check /live-monitor and Supabase/fallback rows.")
    elif args.live:
        from strategy.load_spec import read_raw_spec

        def _run_live_paper_loop(config: Config, exchange_symbol: str, live_seconds: int = 0):
            manage_pid()
            print("STARTING LIVE PAPER TRADING LOOP")
            feed = LiveMarketFeed(exchange_symbol, timeframe=str(config.TIMEFRAME))
            raw = read_raw_spec()
            hybrid_hook = HybridScoringHook(raw)
            exits_cfg = raw.get("exits") if isinstance(raw, dict) else {}
            universe_cfg = raw.get("trading_universe") if isinstance(raw, dict) else {}
            d = feed.fetch_live_candles(limit=220)
            d = d.set_index("timestamp", drop=False)
            d = ICTEngine(config).process_dataframe(d)
            position = None
            started = time.time()
            audit_path = Path("data/signal_audit.jsonl")
            audit_path.parent.mkdir(parents=True, exist_ok=True)

            while True:
                try:
                    # 1) price maintenance every poll
                    live_price = feed.fetch_current_price()
                    regime_live = detect_regime_live(d, lookback=40)
                    print(f"live_price={live_price:.2f} regime={regime_live}")

                    # 2) evaluate signals only on new candle boundaries
                    new_candle = feed.check_new_candle()
                    if new_candle is not None:
                        ts = pd.to_datetime(new_candle["timestamp"], utc=True, errors="coerce")
                        row = {
                            "timestamp": ts,
                            "open": float(new_candle["open"]),
                            "high": float(new_candle["high"]),
                            "low": float(new_candle["low"]),
                            "close": float(new_candle["close"]),
                            "volume": float(new_candle["volume"]),
                        }
                        d = pd.concat([d, pd.DataFrame([row]).set_index("timestamp", drop=False)], axis=0)
                        if len(d) > 500:
                            d = d.iloc[-500:].copy()
                        d = ICTEngine(config).process_dataframe(d)
                        last = d.iloc[-1]
                        regime_state = str(last.get("regime_state") or "unknown")
                        allowed, reason = should_open_position(
                            regime_state, universe_cfg if isinstance(universe_cfg, dict) else {}
                        )
                        signal = int(last.get("signal", 0) or 0)
                        signal_strength = float(last.get("signal_strength", 0.0) or 0.0)
                        confluence_n = int(confluence_breakdown(last, config).get("count", 0))

                        signal_obj = {
                            "side": "LONG" if signal == 1 else ("SHORT" if signal == -1 else ""),
                            "strength": signal_strength,
                            "confluence": confluence_n,
                        }
                        direction = "long" if signal == 1 else ("short" if signal == -1 else "neutral")
                        signals_for_hook = [
                            {"direction": direction, "confluence_score": float(signal_strength)}
                        ]
                        try:
                            signals_for_hook = hybrid_hook.apply(signals_for_hook, d.copy())
                        except Exception as e:
                            logger.error(
                                "HybridScoringHook failed: %s. Falling back to ICT-only.",
                                e,
                                exc_info=True,
                            )
                        decision = "SKIP"
                        skip_reason = "no_confluence"
                        if not allowed:
                            skip_reason = reason
                        elif signal == 0:
                            skip_reason = "no_signal"
                        elif signal_strength < float(config.MIN_SIGNAL_STRENGTH):
                            skip_reason = "strength_below_threshold"
                        elif confluence_n < int(config.MIN_CONFLUENCE):
                            skip_reason = "confluence_below_threshold"
                        else:
                            decision = "ENTER"
                            skip_reason = None

                        audit = merge_pattern_flags(
                            {
                                "ts": datetime.now(timezone.utc).isoformat(),
                                "symbol": exchange_symbol.replace("/", ""),
                                "regime": regime_state,
                                "price": float(live_price),
                                "decision": decision,
                                "skip_reason": skip_reason,
                                "confluence": confluence_n,
                                "signal_strength": signal_strength,
                            },
                            last,
                        )
                        append_signal_audit_jsonl(audit, audit_path)

                        if bool(args.debug_signals):
                            print(
                                "signal_debug "
                                f"regime_live={regime_live} regime_state={regime_state} "
                                f"signal={signal} strength={signal_strength:.1f} confluence={confluence_n} "
                                f"allowed={allowed} reason={reason}"
                            )

                        if (
                            position is None
                            and decision == "ENTER"
                            and signal_obj["side"] in {"LONG", "SHORT"}
                        ):
                            entry_price = live_price
                            atr = float(atr_at_index(d, len(d) - 1, period=14))
                            levels = compute_sl_tp(signal, entry_price, last, atr, config)
                            stop = float(levels["stop_loss"])
                            position = {
                                "side": signal_obj["side"],
                                "direction": "long" if signal == 1 else "short",
                                "entry_price": entry_price,
                                "stop_price": stop,
                                "stop_distance": abs(entry_price - stop),
                                "entry_time": datetime.now(timezone.utc).isoformat(),
                                "regime": regime_state,
                            }
                            print(f"PAPER ENTRY: {position}")
                        elif position is not None:
                            if position["direction"] == "long":
                                unrealized_r = (live_price - position["entry_price"]) / max(position["stop_distance"], 1e-9)
                            else:
                                unrealized_r = (position["entry_price"] - live_price) / max(position["stop_distance"], 1e-9)
                            state = {
                                "entry_price": float(position["entry_price"]),
                                "stop_price": float(position["stop_price"]),
                                "stop_distance": float(position["stop_distance"]),
                                "direction": str(position["direction"]),
                                "unrealized_r": float(unrealized_r),
                                "high_since_entry": float(d["high"].iloc[-5:].max()),
                                "low_since_entry": float(d["low"].iloc[-5:].min()),
                            }
                            apply_regime_exit_logic(state, exits_cfg if isinstance(exits_cfg, dict) else {}, str(position["regime"]))
                            position["stop_price"] = float(state.get("stop_price", position["stop_price"]))

                            should_exit = False
                            if position["direction"] == "long" and live_price <= position["stop_price"]:
                                should_exit = True
                            if position["direction"] == "short" and live_price >= position["stop_price"]:
                                should_exit = True
                            if should_exit:
                                pnl = (live_price - position["entry_price"]) * (1.0 if position["direction"] == "long" else -1.0)
                                r_mult = pnl / max(position["stop_distance"], 1e-9)
                                rec = {
                                    "id": f"{exchange_symbol}-{datetime.now(timezone.utc).timestamp()}",
                                    "symbol": exchange_symbol.replace("/", ""),
                                    "regime": position["regime"],
                                    "entry_price": float(position["entry_price"]),
                                    "exit_price": float(live_price),
                                    "r_multiple": float(r_mult),
                                    "exit_reason": "STOP_RULE",
                                    "pnl_usd": float(pnl),
                                    "exit_time": datetime.now(timezone.utc).isoformat(),
                                    "paper_mode": True,
                                }
                                log_trade_to_supabase(rec)
                                print(f"PAPER EXIT: {rec}")
                                position = None

                    if live_seconds > 0 and (time.time() - started) >= live_seconds:
                        print("live loop completed requested duration")
                        break
                    time.sleep(5)
                except Exception as e:
                    print(f"Live loop error: {e}. Retrying in 10s...")
                    time.sleep(10)

        _run_live_paper_loop(cfg, str(args.symbol), int(args.live_seconds or 0))
    elif args.dry_run:
        from backtester import Backtester

        out = Backtester(cfg, record_playbook=False).run(verbose=False)
        trades = out.get("trades", [])[:3]
        print(f"dry_run trades={len(out.get('trades', []))}")
        for i, t in enumerate(trades, 1):
            print(
                f"trade_{i}: symbol={cfg.SYMBOL} side={t.get('side')} "
                f"entry={t.get('entry_price')} exit={t.get('exit_price')} "
                f"r={t.get('r_multiple')} reason={t.get('exit_reason', t.get('exit_type'))}"
            )
    else:
        print("paper_trader.py direct mode requires app socket context; use app.py for long-running PAPER loop.")
