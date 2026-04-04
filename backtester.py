from __future__ import annotations

import pandas as pd
import numpy as np
from datetime import datetime
from typing import Any, Dict, Optional

from ict_engine import ICTEngine
from data_handler import DataHandler
from config import Config
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
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


class Backtester:
    def __init__(self, config: Config, *, record_playbook: bool = True):
        self.config = config
        self.record_playbook = record_playbook
        self.ict = ICTEngine(config)
        self.data_handler = DataHandler(config)
        self.trades = []
        self.equity_curve = []
        self._pb_entry_json: Optional[str] = None
        self._pb_entry_text: Optional[str] = None
        self._entry_regime_state = "unknown"
        self._entry_regime_gate_allowed = True

    def run(self, *, verbose: bool = True) -> Dict:
        """Run full backtest. Set verbose=False for research sweeps (no stdout spam)."""
        if verbose:
            print("🔄 Fetching historical data...")
        df = self.data_handler.fetch_historical_data(
            self.config.BACKTEST_START_DATE, self.config.BACKTEST_END_DATE
        )

        if verbose:
            print("🔍 Running ICT analysis...")
        df = self.ict.process_dataframe(df)

        if verbose:
            print("💰 Simulating trades with scale-out + trail...")
        df = self._simulate_trades(df, verbose=verbose)

        if verbose:
            print("📊 Calculating performance metrics...")
        metrics = self._calculate_metrics(df)
        regime_summary = self._regime_summary(df, self.trades)
        if isinstance(metrics, dict):
            metrics["regime_summary"] = regime_summary

        if verbose:
            self._print_diagnostic(metrics)

        return {"df": df, "trades": self.trades, "metrics": metrics, "equity_curve": self.equity_curve}

    @staticmethod
    def run_multi(
        *,
        base_config: Config,
        symbols: list[str],
        timeframe: str,
        start_date: str,
        end_date: str,
        initial_capital: float,
        max_workers: int | None = None,
        verbose: bool = False,
    ) -> Dict:
        """
        Parallel multi-coin backtest (SOL/ETH/BTC etc) using identical genes per symbol.

        Safety/robustness notes:
        - Default disables playbook recording to avoid SQLite write contention.
        - Uses separate Backtester+DataHandler instances per worker (no shared session state).
        """

        clean_syms = [str(s).upper().replace("/", "") for s in symbols if s]
        clean_syms = list(dict.fromkeys(clean_syms))  # stable de-dupe
        if not clean_syms:
            return {"success": False, "error": "No symbols provided"}

        def _clone_for(sym: str) -> Config:
            c = Config()
            # Core runtime fields
            for attr in (
                "BINANCE_API",
                "TIMEFRAME",
                "SYMBOL",
                "INITIAL_CAPITAL",
                "BACKTEST_START_DATE",
                "BACKTEST_END_DATE",
                "COMMISSION",
                "SLIPPAGE",
                "MAX_CANDLES_HOLD",
                "TP1_RATIO",
                "TP2_RATIO",
                "TP3_RATIO",
                "TP1_PCT",
                "TP2_PCT",
                "TP3_PCT",
                "TRAIL_AFTER_TP1",
                "TRAIL_ATR_MULTIPLIER",
            ):
                if hasattr(base_config, attr) and hasattr(c, attr):
                    setattr(c, attr, getattr(base_config, attr))
            # Genes (lock to base_config)
            for attr in (
                "ICT_RANGE_HOURS",
                "LIQUIDITY_BUFFER",
                "FVG_THRESHOLD",
                "OTE_LEVELS",
                "RISK_PER_TRADE",
                "ATR_MULTIPLIER",
                "MIN_CONFLUENCE",
                "MIN_SIGNAL_STRENGTH",
                "REGIME_GATE_ENABLED",
                "REGIME_ADX_PERIOD",
                "REGIME_ADX_MIN",
                "REGIME_ATR_PERIOD",
                "REGIME_ATR_PCT_MIN",
                "REGIME_EMA_FAST",
                "REGIME_EMA_SLOW",
                "REGIME_PERSIST_BARS",
                "REGIME_RANGE_MIN_SIGNAL_STRENGTH",
                "REGIME_RANGE_MIN_CONFLUENCE",
            ):
                if hasattr(base_config, attr) and hasattr(c, attr):
                    setattr(c, attr, getattr(base_config, attr))

            c.SYMBOL = sym
            c.TIMEFRAME = timeframe
            c.BACKTEST_START_DATE = start_date
            c.BACKTEST_END_DATE = end_date
            c.INITIAL_CAPITAL = float(initial_capital)
            return c

        def _run_one(sym: str, _verbose: bool = verbose) -> Dict:
            cfg = _clone_for(sym)
            bt = Backtester(cfg, record_playbook=False)
            out = bt.run(verbose=_verbose)
            return {"symbol": sym, **out}

        workers = int(max_workers or min(3, max(1, (os.cpu_count() or 2))))
        workers = max(1, min(workers, len(clean_syms)))

        per_symbol: Dict[str, Any] = {}
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(_run_one, s): s for s in clean_syms}
            for fut in as_completed(futs):
                sym = futs[fut]
                try:
                    per_symbol[sym] = fut.result()
                except Exception as e:
                    per_symbol[sym] = {"symbol": sym, "error": str(e)}

        # Aggregate metrics (robustness-first): worst DD, min PF, mean/median Sharpe.
        metrics_list = [v.get("metrics") for v in per_symbol.values() if isinstance(v, dict) and v.get("metrics")]
        ok = [m for m in metrics_list if isinstance(m, dict) and not m.get("error")]
        if not ok:
            return {"success": False, "error": "All symbol backtests failed", "per_symbol": per_symbol}

        def _f(m, k, default=0.0):
            try:
                return float(m.get(k, default))
            except Exception:
                return float(default)

        sharpes = [_f(m, "sharpe_ratio") for m in ok]
        pfs = [_f(m, "profit_factor") for m in ok]
        # max_drawdown in metrics is a NEGATIVE percent (e.g. -8.2 means -8.2%).
        dds_pct = [_f(m, "max_drawdown") for m in ok]
        total_trades = sum(int(m.get("total_trades", 0) or 0) for m in ok)

        sharpes_sorted = sorted(sharpes)
        median_sharpe = sharpes_sorted[len(sharpes_sorted) // 2]

        worst_dd_pct = float(min(dds_pct))  # most negative = worst drawdown
        worst_dd_abs = abs(worst_dd_pct)

        # Regime-aware robustness score (simple, conservative):
        # (mean_Sharpe * 0.4) + (min_PF * 0.3) + (-worst_DD * 0.3)
        # worst_DD is negative pct, so -worst_DD is positive.
        mean_sharpe = float(sum(sharpes) / max(1, len(sharpes)))
        min_sharpe = float(min(sharpes)) if sharpes else 0.0
        min_pf = float(min(pfs))
        robustness_score = (mean_sharpe * 0.4) + (min_pf * 0.3) + ((-worst_dd_pct / 100.0) * 0.3)

        warnings: list[str] = []
        if worst_dd_abs > 12.0:
            warnings.append(
                f"DD guard (HARD): at least one symbol exceeded 12% max drawdown (worst: {worst_dd_abs:.2f}%)."
            )
        if worst_dd_pct < -10.0:
            warnings.append(
                f"DD guard (TARGET): worst max drawdown breached -10% target (worst: {worst_dd_abs:.2f}%). "
                "Conservative suggestion: consider raising MIN_SIGNAL_STRENGTH (+2 to +5) and/or MIN_CONFLUENCE (+1), "
                "or reducing RISK_PER_TRADE (e.g. 0.01→0.0075) if evolution allows."
            )

        # Per-coin summary table for quick robustness scan.
        per_coin_summary: list[dict[str, Any]] = []
        for sym in clean_syms:
            m = (per_symbol.get(sym) or {}).get("metrics") if isinstance(per_symbol.get(sym), dict) else None
            if not isinstance(m, dict) or m.get("error"):
                per_coin_summary.append({"symbol": sym, "error": (m or {}).get("error") if isinstance(m, dict) else "error"})
                continue
            rs = m.get("regime_summary") if isinstance(m.get("regime_summary"), dict) else {}
            per_coin_summary.append(
                {
                    "symbol": sym,
                    "sharpe": _f(m, "sharpe_ratio"),
                    "profit_factor": _f(m, "profit_factor"),
                    "max_dd_pct": _f(m, "max_drawdown"),
                    "trades": int(m.get("total_trades", 0) or 0),
                    "bars_gate_removed": int(rs.get("bars_regime_gate_removed", 0) or 0),
                    "pct_entries_ranging": rs.get("pct_unique_entries_in_ranging"),
                }
            )

        gate_removed_sum = sum(int((x or {}).get("bars_gate_removed", 0) or 0) for x in per_coin_summary)
        aggregate = {
            "symbols": clean_syms,
            "timeframe": timeframe,
            "start_date": start_date,
            "end_date": end_date,
            "initial_capital_each": float(initial_capital),
            "mean_sharpe": round(float(mean_sharpe), 3),
            "min_sharpe": round(float(min_sharpe), 3),
            "median_sharpe": round(float(median_sharpe), 3),
            "min_profit_factor": round(float(min_pf), 3),
            "worst_max_drawdown_pct": round(float(worst_dd_pct), 3),
            "robustness_score": round(float(robustness_score), 4),
            "warnings": warnings,
            "per_coin_summary": per_coin_summary,
            "total_trades_all": int(total_trades),
            "bars_regime_gate_removed_all_symbols": int(gate_removed_sum),
        }

        if verbose and warnings:
            print("run_multi warnings:")
            for w in warnings:
                print(f"- {w}")

        return {"success": True, "aggregate": aggregate, "per_symbol": per_symbol}

    def _simulate_trades(self, df: pd.DataFrame, *, verbose: bool = False) -> pd.DataFrame:
        """
        Professional trade simulation with:
        - ATR-based stop
        - 3-tier profit taking (scale-out)
        - Trailing stop after TP1
        - Time-based exit

        Note: Our dataframes are timestamp-indexed; we iterate by integer position.
        """
        df = df.copy()
        capital = float(self.config.INITIAL_CAPITAL)

        # Position state
        position = 0  # 0: None, 1: Long, -1: Short
        entry_price = 0.0
        entry_i = 0
        stop_loss = 0.0
        tp1 = tp2 = tp3 = 0.0
        position_remaining = 1.0
        atr_at_entry = 0.0
        stop_at_entry = 0.0  # for risk sizing (matches live / ICT levels)

        df["portfolio_value"] = capital
        df["position"] = 0
        df["open_pnl"] = 0.0

        for i in range(len(df)):
            row = df.iloc[i]
            ts = row["timestamp"]

            # === EXIT LOGIC (Check first) ===
            if position != 0:
                candle_count = i - entry_i

                # Time-based exit
                if candle_count >= int(self.config.MAX_CANDLES_HOLD):
                    pnl = self._close_position(
                        float(row["close"]), position, entry_price, position_remaining, capital, stop_at_entry
                    )
                    capital += pnl
                    self._record_trade(
                        entry_price,
                        float(row["close"]),
                        position,
                        pnl,
                        df.iloc[entry_i]["timestamp"],
                        ts,
                        "TIME_EXIT",
                        position_remaining,
                        atr_at_entry,
                        capital_after=capital,
                        exit_row=row,
                        bars_in_trade=candle_count,
                    )
                    position = 0
                    position_remaining = 1.0
                    self._clear_playbook_snap()

                # Scale-out profit taking + stops only if still open
                if position != 0 and position_remaining > 0:
                    # TP1: 50% at 1:1 R:R
                    if position_remaining == 1.0 and check_tp_hit(position, row, tp1):
                        pnl = self._close_partial(
                            position, float(row["close"]), entry_price, self.config.TP1_PCT, capital, stop_at_entry
                        )
                        capital += pnl
                        position_remaining = max(0.0, position_remaining - float(self.config.TP1_PCT))
                        self._record_trade(
                            entry_price,
                            float(row["close"]),
                            position,
                            pnl,
                            df.iloc[entry_i]["timestamp"],
                            ts,
                            "TP1",
                            float(self.config.TP1_PCT),
                            atr_at_entry,
                            capital_after=capital,
                            exit_row=row,
                            bars_in_trade=candle_count,
                        )

                    # TP2: 30% at 2:1 R:R
                    if position_remaining > 0 and position_remaining <= 0.50 and check_tp_hit(position, row, tp2):
                        pnl = self._close_partial(
                            position, float(row["close"]), entry_price, self.config.TP2_PCT, capital, stop_at_entry
                        )
                        capital += pnl
                        position_remaining = max(0.0, position_remaining - float(self.config.TP2_PCT))
                        self._record_trade(
                            entry_price,
                            float(row["close"]),
                            position,
                            pnl,
                            df.iloc[entry_i]["timestamp"],
                            ts,
                            "TP2",
                            float(self.config.TP2_PCT),
                            atr_at_entry,
                            capital_after=capital,
                            exit_row=row,
                            bars_in_trade=candle_count,
                        )

                    # TP3: 20% at 3:1 R:R
                    if position_remaining > 0 and position_remaining <= 0.20 and check_tp_hit(position, row, tp3):
                        pnl = self._close_partial(
                            position, float(row["close"]), entry_price, self.config.TP3_PCT, capital, stop_at_entry
                        )
                        capital += pnl
                        position_remaining = max(0.0, position_remaining - float(self.config.TP3_PCT))
                        self._record_trade(
                            entry_price,
                            float(row["close"]),
                            position,
                            pnl,
                            df.iloc[entry_i]["timestamp"],
                            ts,
                            "TP3",
                            float(self.config.TP3_PCT),
                            atr_at_entry,
                            capital_after=capital,
                            exit_row=row,
                            bars_in_trade=candle_count,
                        )
                        if position_remaining <= 0:
                            position = 0
                            position_remaining = 1.0
                            self._clear_playbook_snap()

                    # Stop loss on remaining position
                    if position != 0 and position_remaining > 0 and check_sl_hit(position, row, stop_loss):
                        pnl = self._close_partial(
                            position, float(row["close"]), entry_price, position_remaining, capital, stop_at_entry
                        )
                        capital += pnl
                        self._record_trade(
                            entry_price,
                            float(row["close"]),
                            position,
                            pnl,
                            df.iloc[entry_i]["timestamp"],
                            ts,
                            "STOP_LOSS",
                            position_remaining,
                            atr_at_entry,
                            capital_after=capital,
                            exit_row=row,
                            bars_in_trade=candle_count,
                        )
                        position = 0
                        position_remaining = 1.0
                        self._clear_playbook_snap()

                    # Trailing stop (after TP1 hit)
                    if (
                        position != 0
                        and position_remaining < 1.0
                        and position_remaining > 0
                        and bool(self.config.TRAIL_AFTER_TP1)
                    ):
                        trail_stop = trail_stop_price(position, entry_price, atr_at_entry, row, self.config)
                        if check_sl_hit(position, row, trail_stop):
                            pnl = self._close_partial(
                                position, float(row["close"]), entry_price, position_remaining, capital, stop_at_entry
                            )
                            capital += pnl
                            self._record_trade(
                                entry_price,
                                float(row["close"]),
                                position,
                                pnl,
                                df.iloc[entry_i]["timestamp"],
                                ts,
                                "TRAIL_STOP",
                                position_remaining,
                                atr_at_entry,
                                capital_after=capital,
                                exit_row=row,
                                bars_in_trade=candle_count,
                            )
                            position = 0
                            position_remaining = 1.0
                            self._clear_playbook_snap()

                # Signal reversal exit (early)
                if (
                    position != 0
                    and int(row.get("signal", 0)) != 0
                    and int(row.get("signal", 0)) != position
                    and float(row.get("signal_strength", 0)) >= float(self.config.MIN_SIGNAL_STRENGTH)
                ):
                    pnl = self._close_partial(
                        position, float(row["close"]), entry_price, position_remaining, capital, stop_at_entry
                    )
                    capital += pnl
                    self._record_trade(
                        entry_price,
                        float(row["close"]),
                        position,
                        pnl,
                        df.iloc[entry_i]["timestamp"],
                        ts,
                        "SIGNAL_REVERSAL",
                        position_remaining,
                        atr_at_entry,
                        capital_after=capital,
                        exit_row=row,
                        bars_in_trade=candle_count,
                    )
                    position = 0
                    position_remaining = 1.0
                    self._clear_playbook_snap()

            # === ENTRY LOGIC ===
            if position == 0 and int(row.get("signal", 0)) != 0:
                cx = confluence_breakdown(row, self.config)
                confluence_score = int(cx["count"])

                if confluence_score >= int(self.config.MIN_CONFLUENCE) and float(row.get("signal_strength", 0)) >= float(
                    self.config.MIN_SIGNAL_STRENGTH
                ):
                    position = int(row["signal"])
                    entry_price = float(row["close"])
                    entry_i = i
                    position_remaining = 1.0

                    atr_at_entry = float(atr_at_index(df, i, period=14))
                    levels = compute_sl_tp(position, entry_price, row, atr_at_entry, self.config)
                    stop_loss = levels["stop_loss"]
                    stop_at_entry = stop_loss
                    tp1, tp2, tp3 = levels["tp1"], levels["tp2"], levels["tp3"]

                    df.iloc[i, df.columns.get_loc("position")] = position

                    rs = row.get("regime_state")
                    self._entry_regime_state = "unknown" if pd.isna(rs) else str(rs)
                    self._entry_regime_gate_allowed = bool(row.get("regime_gate_allowed", True))

                    if verbose:
                        pretty = format_confluence_pretty(cx, min_confluence_required=int(self.config.MIN_CONFLUENCE))
                        print(f"Entry taken | {self.config.SYMBOL} | {('LONG' if position==1 else 'SHORT')} | {pretty}")

                    ctx, txt = entry_snapshot(
                        df.iloc[i],
                        confluence_score,
                        self.config.SYMBOL,
                        self.config,
                        confluence_reasons=cx.get("reasons"),
                        confluence_flags=cx.get("flags"),
                        confluence_thresholds=cx.get("thresholds"),
                    )
                    self._pb_entry_json = entry_context_json_str(ctx)
                    self._pb_entry_text = txt
                    bt_ts = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
                    if self.record_playbook:
                        record_playbook_event(
                            mode="BACKTEST",
                            symbol=self.config.SYMBOL,
                            timeframe=self.config.TIMEFRAME,
                            event_type="OPEN",
                            side="LONG" if position == 1 else "SHORT",
                            entry_price=float(entry_price),
                            exit_price=None,
                            position_fraction=1.0,
                            pnl=None,
                            capital_after=float(capital),
                            bar_time=bt_ts,
                            entry_reason_json=self._pb_entry_json,
                            entry_reason_text=self._pb_entry_text,
                            exit_reason_text=None,
                        )

            # Update portfolio value
            if position != 0:
                unrealized = unrealized_pnl(
                    position,
                    entry_price,
                    float(row["close"]),
                    position_remaining,
                    capital,
                    stop_at_entry,
                    self.config,
                )
                df.iloc[i, df.columns.get_loc("portfolio_value")] = capital + unrealized
                df.iloc[i, df.columns.get_loc("open_pnl")] = unrealized
            else:
                df.iloc[i, df.columns.get_loc("portfolio_value")] = capital
                df.iloc[i, df.columns.get_loc("open_pnl")] = 0.0

            self.equity_curve.append({"timestamp": ts.isoformat(), "value": float(df.iloc[i]["portfolio_value"])})

        return df

    def _close_position(
        self, exit_price: float, position: int, entry: float, remaining: float, capital: float, stop_price: float
    ) -> float:
        return self._close_partial(position, exit_price, entry, remaining, capital, stop_price)

    def _close_partial(
        self, position: int, exit_price: float, entry: float, pct: float, capital: float, stop_price: float
    ) -> float:
        return close_partial_pnl(position, exit_price, entry, pct, capital, stop_price, self.config)

    def _clear_playbook_snap(self) -> None:
        self._pb_entry_json = None
        self._pb_entry_text = None
        self._entry_regime_state = "unknown"
        self._entry_regime_gate_allowed = True

    def _regime_summary(self, df: pd.DataFrame, trades: list) -> Dict[str, Any]:
        """v1.9 gate + entry-regime stats for A/B backtests (persisted in metrics JSON)."""
        gate_on = bool(getattr(self.config, "REGIME_GATE_ENABLED", False))
        n_bars = int(len(df))
        out: Dict[str, Any] = {
            "regime_gate_enabled": gate_on,
            "bars_total": n_bars,
        }
        if "signal_pre_regime_gate" in df.columns:
            raw = df["signal_pre_regime_gate"].astype(int)
            n_raw = int((raw != 0).sum())
            out["bars_with_raw_ict_signal"] = n_raw
        if "regime_gate_removed" in df.columns:
            n_rm = int(df["regime_gate_removed"].astype(bool).sum())
            out["bars_regime_gate_removed"] = n_rm
            n_raw = int(out.get("bars_with_raw_ict_signal", 0) or 0)
            out["pct_raw_signals_removed_by_gate"] = (
                round((100.0 * n_rm / n_raw), 2) if n_raw > 0 else 0.0
            )
        if n_bars > 0 and "regime_state" in df.columns:
            vc = df["regime_state"].astype(str).value_counts()
            out["bar_regime_mix_pct"] = {str(k): round(100.0 * int(v) / n_bars, 2) for k, v in vc.items()}

        if trades:
            td = pd.DataFrame(trades)
            if "entry_regime_state" in td.columns and len(td) > 0:
                ent = td.drop_duplicates(subset=["entry_time", "side"])
                cts = ent["entry_regime_state"].astype(str).value_counts().to_dict()
                total_e = int(len(ent))
                out["unique_entries_by_regime_state"] = {str(k): int(v) for k, v in cts.items()}
                out["unique_entries_total"] = total_e
                ranging_n = int((ent["entry_regime_state"].astype(str) == "ranging").sum())
                out["pct_unique_entries_in_ranging"] = (
                    round(100.0 * ranging_n / total_e, 2) if total_e > 0 else 0.0
                )
        return out

    def _record_trade(
        self,
        entry: float,
        exit: float,
        side: int,
        pnl: float,
        entry_time,
        exit_time,
        exit_type: str,
        position_pct: float,
        atr: float,
        *,
        capital_after: float,
        exit_row: Optional[pd.Series] = None,
        bars_in_trade: Optional[int] = None,
    ):
        risk_amount = float(self.config.INITIAL_CAPITAL) * float(self.config.RISK_PER_TRADE)
        r_multiple = float(pnl / risk_amount) if risk_amount > 0 else 0.0

        self.trades.append(
            {
                "trade_id": len(self.trades) + 1,
                "entry_time": entry_time.isoformat() if hasattr(entry_time, "isoformat") else str(entry_time),
                "exit_time": exit_time.isoformat() if hasattr(exit_time, "isoformat") else str(exit_time),
                "side": "LONG" if side == 1 else "SHORT",
                "entry_price": float(entry),
                "exit_price": float(exit),
                "pnl": float(pnl),
                "pnl_pct": float(pnl / float(self.config.INITIAL_CAPITAL)) * 100,
                "exit_type": exit_type,
                "position_pct": float(position_pct),
                "r_multiple": r_multiple,
                "atr_at_entry": float(atr),
                "entry_regime_state": str(getattr(self, "_entry_regime_state", "unknown")),
                "entry_regime_gate_allowed": bool(getattr(self, "_entry_regime_gate_allowed", True)),
            }
        )

        ext_txt = (
            exit_narrative(
                exit_type,
                exit_row,
                entry_reason_text=self._pb_entry_text,
                exit_price=float(exit),
                bars_held=bars_in_trade,
                reversal_min_strength=float(getattr(self.config, "MIN_SIGNAL_STRENGTH", 70)),
            )
            if exit_row is not None
            else exit_type
        )
        et = exit_time.isoformat() if hasattr(exit_time, "isoformat") else str(exit_time)
        if self.record_playbook:
            record_playbook_event(
                mode="BACKTEST",
                symbol=self.config.SYMBOL,
                timeframe=self.config.TIMEFRAME,
                event_type=exit_type,
                side="LONG" if side == 1 else "SHORT",
                entry_price=float(entry),
                exit_price=float(exit),
                position_fraction=float(position_pct),
                pnl=float(pnl),
                capital_after=float(capital_after),
                bar_time=et,
                entry_reason_json=self._pb_entry_json,
                entry_reason_text=self._pb_entry_text,
                exit_reason_text=ext_txt,
            )

    def _calculate_metrics(self, df: pd.DataFrame) -> Dict:
        if not self.trades:
            return {"error": "No trades executed"}

        trades_df = pd.DataFrame(self.trades)
        total_trades = len(trades_df)
        winning_trades = int((trades_df["pnl"] > 0).sum())
        losing_trades = int((trades_df["pnl"] <= 0).sum())
        win_rate = (winning_trades / total_trades) * 100 if total_trades > 0 else 0.0

        total_pnl = float(trades_df["pnl"].sum())
        avg_win = float(trades_df.loc[trades_df["pnl"] > 0, "pnl"].mean()) if winning_trades > 0 else 0.0
        avg_loss = float(abs(trades_df.loc[trades_df["pnl"] <= 0, "pnl"].mean())) if losing_trades > 0 else 0.0

        losses_sum = float(trades_df.loc[trades_df["pnl"] < 0, "pnl"].sum())
        profit_factor = abs(total_pnl / losses_sum) if losses_sum != 0 else 999.0
        sharpe_ratio = float(self._calculate_sharpe(df))
        max_drawdown = float(self._calculate_max_drawdown(df))

        avg_r = float(trades_df["r_multiple"].mean()) if "r_multiple" in trades_df.columns else 0.0
        largest_win = float(trades_df["pnl"].max())
        largest_loss = float(trades_df["pnl"].min())

        exit_types = (
            trades_df.groupby("exit_type")["pnl"].agg(["count", "mean"]).to_dict() if "exit_type" in trades_df.columns else {}
        )

        expectancy = (win_rate / 100 * avg_win) - ((1 - win_rate / 100) * avg_loss)

        return {
            "total_trades": total_trades,
            "winning_trades": winning_trades,
            "losing_trades": losing_trades,
            "win_rate": round(win_rate, 2),
            "total_pnl": round(total_pnl, 2),
            "total_pnl_pct": round((total_pnl / float(self.config.INITIAL_CAPITAL)) * 100, 2),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "profit_factor": round(profit_factor, 2),
            "sharpe_ratio": round(sharpe_ratio, 2),
            "max_drawdown": round(max_drawdown * 100, 2),
            "expectancy": round(expectancy, 2),
            "avg_r_multiple": round(avg_r, 2),
            "largest_win": round(largest_win, 2),
            "largest_loss": round(largest_loss, 2),
            "initial_capital": float(self.config.INITIAL_CAPITAL),
            "final_capital": round(float(self.config.INITIAL_CAPITAL) + total_pnl, 2),
            "exit_types": exit_types,
        }

    def _calculate_sharpe(self, df: pd.DataFrame) -> float:
        returns = df["portfolio_value"].pct_change().dropna()
        if returns.std() == 0 or len(returns) < 2:
            return 0.0
        return float((returns.mean() / returns.std()) * np.sqrt(252))

    def _calculate_max_drawdown(self, df: pd.DataFrame) -> float:
        peak = df["portfolio_value"].cummax()
        drawdown = (df["portfolio_value"] - peak) / peak
        return float(drawdown.min())

    def _print_diagnostic(self, metrics: Dict):
        if "error" in metrics:
            print(f"\nBacktest error: {metrics['error']}")
            rs = metrics.get("regime_summary")
            if isinstance(rs, dict):
                print(
                    f"Regime gate:      {'ON' if rs.get('regime_gate_enabled') else 'OFF'} | "
                    f"raw ICT bars: {rs.get('bars_with_raw_ict_signal', '—')} | "
                    f"gate-removed: {rs.get('bars_regime_gate_removed', '—')}"
                )
            return

        print("\n" + "=" * 60)
        print("📊 BACKTEST DIAGNOSTIC REPORT")
        print("=" * 60)
        print(f"Total P&L:        ${metrics['total_pnl']:,.2f} ({metrics['total_pnl_pct']:.2f}%)")
        print(f"Total Trades:     {metrics['total_trades']}")
        print(f"Win Rate:         {metrics['win_rate']:.1f}% ({metrics['winning_trades']}W / {metrics['losing_trades']}L)")
        print(f"Avg Win:          ${metrics['avg_win']:.2f}")
        print(f"Avg Loss:         ${metrics['avg_loss']:.2f}")
        print(f"Profit Factor:    {metrics['profit_factor']:.2f}")
        print(f"Sharpe Ratio:     {metrics['sharpe_ratio']:.2f}")
        print(f"Max Drawdown:     {metrics['max_drawdown']:.2f}%")
        print(f"Avg R-Multiple:   {metrics['avg_r_multiple']:.2f}R")
        print(f"Expectancy/Trade: ${metrics['expectancy']:.2f}")
        print("-" * 60)

        rs = metrics.get("regime_summary")
        if isinstance(rs, dict):
            print(
                f"Regime gate:      {'ON' if rs.get('regime_gate_enabled') else 'OFF'} | "
                f"raw ICT bars: {rs.get('bars_with_raw_ict_signal', '—')} | "
                f"gate-removed: {rs.get('bars_regime_gate_removed', '—')}"
            )
            if rs.get("unique_entries_total"):
                print(
                    f"Entries by regime: {rs.get('unique_entries_by_regime_state')} "
                    f"({float(rs.get('pct_unique_entries_in_ranging') or 0):.1f}% of entries in ranging)"
                )
        print("-" * 60)

        if metrics["avg_loss"] > 0:
            rr_ratio = metrics["avg_win"] / metrics["avg_loss"]
            breakeven_wr = 1 / (1 + rr_ratio) * 100
            edge = "✅ POSITIVE" if metrics["win_rate"] > breakeven_wr else "❌ NEGATIVE"
            print(f"Risk/Reward:      {rr_ratio:.2f}:1")
            print(f"Breakeven WR:     {breakeven_wr:.1f}%")
            print(f"Actual WR:        {metrics['win_rate']:.1f}%")
            print(f"Edge Status:      {edge}")
        print("=" * 60)

