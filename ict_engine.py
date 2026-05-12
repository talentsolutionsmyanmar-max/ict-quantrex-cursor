import pandas as pd
import numpy as np
from regime import annotate_regime
from session_clock import build_kill_zone_min_strength_overlay
from strategy.load_spec import get_kill_zones, read_raw_spec


def _wilder_atr_series(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)
    tr = pd.concat(
        [
            (high - low),
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1.0 / max(1, int(period)), adjust=False).mean().fillna(0.0)


class ICTEngine:
    def __init__(self, config):
        self.config = config
        self.liquidity_highs = []
        self.liquidity_lows = []
        self.fvg_zones = []

    def detect_liquidity_pools(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()

        df["swing_high"] = (
            (df["high"].shift(2) < df["high"].shift(1))
            & (df["high"] > df["high"].shift(1))
            & (df["high"] > df["high"].shift(-1))
            & (df["high"] > df["high"].shift(-2))
        )

        df["swing_low"] = (
            (df["low"].shift(2) > df["low"].shift(1))
            & (df["low"] < df["low"].shift(1))
            & (df["low"] < df["low"].shift(-1))
            & (df["low"] < df["low"].shift(-2))
        )

        self.liquidity_highs = df.loc[df["swing_high"], "high"].dropna().tolist()
        self.liquidity_lows = df.loc[df["swing_low"], "low"].dropna().tolist()

        # Correct forward-filled previous swing levels
        df["liquidity_high_prev"] = pd.Series(np.where(df["swing_high"], df["high"], np.nan), index=df.index).ffill()
        df["liquidity_low_prev"] = pd.Series(np.where(df["swing_low"], df["low"], np.nan), index=df.index).ffill()

        return df

    def detect_fvg(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()

        # Candle 1 at t, candle 3 at t+2
        c1_high = df["high"]
        c1_low = df["low"]
        c3_low = df["low"].shift(-2)
        c3_high = df["high"].shift(-2)

        gap_bull = c3_low - c1_high
        gap_bear = c1_low - c3_high
        method = str(getattr(self.config, "FVG_METHOD", "static") or "static").lower()

        if method == "adaptive":
            atr = _wilder_atr_series(df, period=14)
            min_gap = atr * float(getattr(self.config, "FVG_MIN_GAP_ATR", 0.3))
            df["bullish_fvg"] = (c3_low > c1_high) & (gap_bull > min_gap)
            df["bearish_fvg"] = (c3_high < c1_low) & (gap_bear > min_gap)
        else:
            thr = df["close"] * float(self.config.FVG_THRESHOLD)
            df["bullish_fvg"] = (c3_low > c1_high) & (gap_bull > thr)
            df["bearish_fvg"] = (c3_high < c1_low) & (gap_bear > thr)

        return df

    def calculate_premium_discount(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        window = max(1, int(self.config.ICT_RANGE_HOURS * 4))

        df["range_high"] = df["high"].rolling(window=window, min_periods=window).max()
        df["range_low"] = df["low"].rolling(window=window, min_periods=window).min()
        df["range_mid"] = (df["range_high"] + df["range_low"]) / 2

        df["premium"] = df["close"] > df["range_mid"]
        df["discount"] = df["close"] < df["range_mid"]
        df["equilibrium"] = df["range_mid"]

        return df

    def detect_ote(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()

        df["impulse_high"] = df["high"].rolling(window=20, min_periods=20).max()
        df["impulse_low"] = df["low"].rolling(window=20, min_periods=20).min()
        df["impulse_range"] = df["impulse_high"] - df["impulse_low"]

        for level in self.config.OTE_LEVELS:
            df[f"ote_long_{level}"] = df["impulse_high"] - (df["impulse_range"] * level)
            df[f"ote_short_{level}"] = df["impulse_low"] + (df["impulse_range"] * level)

        return df

    def liquidity_sweep_detection(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()

        factor = float(getattr(self.config, "SWEEP_VOLUME_SPIKE_FACTOR", 0) or 0)
        if factor > 0 and "volume" in df.columns:
            vol = pd.to_numeric(df["volume"], errors="coerce").fillna(0.0)
            vma = vol.rolling(20, min_periods=1).mean()
            vol_ok = vol >= factor * vma.replace(0.0, np.nan)
            vol_ok = vol_ok.fillna(False)
        else:
            vol_ok = pd.Series(True, index=df.index)

        df["bullish_sweep"] = (
            df["liquidity_low_prev"].notna()
            & (df["low"] < df["liquidity_low_prev"])
            & (df["close"] > df["liquidity_low_prev"])
            & (df["discount"])
            & vol_ok
        )

        df["bearish_sweep"] = (
            df["liquidity_high_prev"].notna()
            & (df["high"] > df["liquidity_high_prev"])
            & (df["close"] < df["liquidity_high_prev"])
            & (df["premium"])
            & vol_ok
        )

        return df

    def generate_signal(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df["signal"] = 0
        df["signal_strength"] = 0.0
        df["confluence_count_ict"] = 0
        df["regime_gate_allowed"] = True
        df["regime_gate_reason"] = "disabled"

        long_condition = df["discount"] & (df["bullish_sweep"] | df["bullish_fvg"])
        short_condition = df["premium"] & (df["bearish_sweep"] | df["bearish_fvg"])

        # Coolish-style regime branch: optional FVG confirmation in ranging / directional trends.
        try:
            raw = read_raw_spec()
            reg = raw.get("regime") if isinstance(raw, dict) else {}
            acts = (reg or {}).get("regime_actions") if isinstance(reg, dict) else {}
            rng = acts.get("ranging") if isinstance(acts, dict) else {}
            require_fvg = bool((rng or {}).get("require_fvg_confirmation", False))
            td = acts.get("trend_down") if isinstance(acts, dict) else {}
            if not isinstance(td, dict):
                td = {}
            require_fvg_td = bool(td.get("require_fvg_confirmation", False))
            tu = acts.get("trend_up") if isinstance(acts, dict) else {}
            if not isinstance(tu, dict):
                tu = {}
            require_fvg_tu = bool(tu.get("require_fvg_confirmation", False))
            if "regime_state" in df.columns:
                is_range = df["regime_state"].astype(str).eq("ranging")
                is_td = df["regime_state"].astype(str).eq("trend_down")
                is_tu = df["regime_state"].astype(str).eq("trend_up")
                if require_fvg:
                    long_condition = long_condition & (~is_range | df["bullish_fvg"].astype(bool))
                    short_condition = short_condition & (~is_range | df["bearish_fvg"].astype(bool))
                if require_fvg_td:
                    long_condition = long_condition & (~is_td | df["bullish_fvg"].astype(bool))
                    short_condition = short_condition & (~is_td | df["bearish_fvg"].astype(bool))
                if require_fvg_tu:
                    long_condition = long_condition & (~is_tu | df["bullish_fvg"].astype(bool))
                    short_condition = short_condition & (~is_tu | df["bearish_fvg"].astype(bool))
        except Exception:
            pass

        df.loc[long_condition, "signal"] = 1
        df.loc[short_condition, "signal"] = -1

        # Vectorized strength (same scoring as previous row-wise implementation):
        # sweep 30 + fvg 25 + (discount/premium) 25 + OTE(first configured level) 20, capped at 100.
        #
        # NOTE: The scoring weights are part of the model definition (not evolved genes).
        # Genes control thresholds/geometry; strength is a deterministic composite.
        sig = df["signal"].astype(int)
        sweep = np.where((sig == 1) & df.get("bullish_sweep", False), 30, 0) + np.where(
            (sig == -1) & df.get("bearish_sweep", False), 30, 0
        )
        fvg = np.where((sig == 1) & df.get("bullish_fvg", False), 25, 0) + np.where(
            (sig == -1) & df.get("bearish_fvg", False), 25, 0
        )
        pd_ctx = np.where((sig == 1) & df.get("discount", False), 25, 0) + np.where(
            (sig == -1) & df.get("premium", False), 25, 0
        )
        # Use first OTE level from config to avoid hardcoding 0.62.
        ote_levels = list(getattr(self.config, "OTE_LEVELS", []) or [])
        ote_key = str(ote_levels[0]) if ote_levels else "0.62"
        ote_long = df.get(f"ote_long_{ote_key}")
        ote_short = df.get(f"ote_short_{ote_key}")
        ote = np.zeros(len(df), dtype=float)
        ote_hit = np.zeros(len(df), dtype=bool)
        sweep_hit = np.zeros(len(df), dtype=bool)
        fvg_hit = np.zeros(len(df), dtype=bool)
        pdctx_hit = np.zeros(len(df), dtype=bool)
        if ote_long is not None:
            hit = (sig == 1) & ote_long.notna() & (df["close"] > ote_long)
            ote = ote + np.where(hit, 20, 0)
            ote_hit = ote_hit | hit.to_numpy(dtype=bool, copy=False)
        if ote_short is not None:
            hit = (sig == -1) & ote_short.notna() & (df["close"] < ote_short)
            ote = ote + np.where(hit, 20, 0)
            ote_hit = ote_hit | hit.to_numpy(dtype=bool, copy=False)
        df["ote_hit"] = ote_hit.astype(bool)

        sweep_hit = (((sig == 1) & df.get("bullish_sweep", False)) | ((sig == -1) & df.get("bearish_sweep", False))).astype(bool)
        fvg_hit = (((sig == 1) & df.get("bullish_fvg", False)) | ((sig == -1) & df.get("bearish_fvg", False))).astype(bool)
        pdctx_hit = (((sig == 1) & df.get("discount", False)) | ((sig == -1) & df.get("premium", False))).astype(bool)
        df["confluence_count_ict"] = (
            sweep_hit.astype(int) + fvg_hit.astype(int) + pdctx_hit.astype(int) + df["ote_hit"].astype(int)
        ).astype(int)

        strength = sweep + fvg + pd_ctx + ote
        df["signal_strength"] = np.where(sig != 0, np.minimum(strength, 100.0), 0.0).astype(float)

        df = self._apply_kill_zone_strength_floor(df)

        # Snapshot before optional regime gate (for backtest A/B: how many raw ICT bars were zeroed).
        df["signal_pre_regime_gate"] = df["signal"].astype(int)

        # Optional v1.9 regime gate: ranging stricter thresholds; optional trend_down / trend_up overlays from spec.
        gate_on = bool(getattr(self.config, "REGIME_GATE_ENABLED", False))
        if gate_on and "regime_state" in df.columns:
            range_strength_min = float(getattr(self.config, "REGIME_RANGE_MIN_SIGNAL_STRENGTH", 75))
            range_conf_min = int(getattr(self.config, "REGIME_RANGE_MIN_CONFLUENCE", 3))
            is_range = df["regime_state"].astype(str).eq("ranging")
            is_td = df["regime_state"].astype(str).eq("trend_down")
            is_tu = df["regime_state"].astype(str).eq("trend_up")
            base_sig = df["signal"].astype(int) != 0
            strict_ok = (df["signal_strength"] >= range_strength_min) & (df["confluence_count_ict"] >= range_conf_min)
            td_block: dict = {}
            tu_block: dict = {}
            try:
                raw = read_raw_spec()
                reg = raw.get("regime") if isinstance(raw, dict) else {}
                acts = (reg or {}).get("regime_actions") if isinstance(reg, dict) else {}
                cand_td = acts.get("trend_down") if isinstance(acts, dict) else {}
                td_block = cand_td if isinstance(cand_td, dict) else {}
                cand_tu = acts.get("trend_up") if isinstance(acts, dict) else {}
                tu_block = cand_tu if isinstance(cand_tu, dict) else {}
            except Exception:
                td_block = {}
                tu_block = {}
            has_td_gate = ("min_signal_strength" in td_block) or ("min_confluence" in td_block)
            if has_td_gate:
                td_s = (
                    float(td_block["min_signal_strength"])
                    if td_block.get("min_signal_strength") is not None
                    else float(getattr(self.config, "MIN_SIGNAL_STRENGTH", 72))
                )
                td_c = (
                    int(td_block["min_confluence"])
                    if td_block.get("min_confluence") is not None
                    else int(getattr(self.config, "MIN_CONFLUENCE", 3))
                )
                strict_td_ok = (df["signal_strength"] >= td_s) & (df["confluence_count_ict"] >= td_c)
            else:
                strict_td_ok = pd.Series(True, index=df.index)

            has_tu_gate = ("min_signal_strength" in tu_block) or ("min_confluence" in tu_block)
            if has_tu_gate:
                tu_s = (
                    float(tu_block["min_signal_strength"])
                    if tu_block.get("min_signal_strength") is not None
                    else float(getattr(self.config, "MIN_SIGNAL_STRENGTH", 72))
                )
                tu_c = (
                    int(tu_block["min_confluence"])
                    if tu_block.get("min_confluence") is not None
                    else int(getattr(self.config, "MIN_CONFLUENCE", 3))
                )
                strict_tu_ok = (df["signal_strength"] >= tu_s) & (df["confluence_count_ict"] >= tu_c)
            else:
                strict_tu_ok = pd.Series(True, index=df.index)

            allow = (~base_sig) | ((~is_range | strict_ok) & (~is_td | strict_td_ok) & (~is_tu | strict_tu_ok))
            gated = df["signal"].where(allow, 0).astype(int)
            removed = (df["signal"].astype(int) != 0) & (gated == 0)

            df["signal"] = gated
            df["regime_gate_allowed"] = allow.astype(bool)
            df["regime_gate_reason"] = np.where(
                ~base_sig,
                "flat",
                np.where(
                    gated.astype(int) != 0,
                    np.where(
                        is_range,
                        "range_allowed_strict",
                        np.where(
                            is_td,
                            "trend_down_allowed_strict",
                            np.where(is_tu, "trend_up_allowed_strict", "trend_allowed"),
                        ),
                    ),
                    np.where(
                        is_range,
                        "range_filtered_low_quality",
                        np.where(
                            is_td,
                            "trend_down_filtered_low_quality",
                            np.where(is_tu, "trend_up_filtered_low_quality", "regime_filtered_low_quality"),
                        ),
                    ),
                ),
            )
            df["regime_gate_removed"] = removed.astype(bool)
        else:
            df["regime_gate_allowed"] = True
            df["regime_gate_reason"] = "disabled"
            df["regime_gate_removed"] = False
        return df

    def _apply_kill_zone_strength_floor(self, df: pd.DataFrame) -> pd.DataFrame:
        """spec sessions.kill_zones[].min_signal_strength — uses each bar's UTC time."""
        df = df.copy()
        base = float(getattr(self.config, "MIN_SIGNAL_STRENGTH", 0))
        ov = build_kill_zone_min_strength_overlay(get_kill_zones())
        if not any(x is not None for x in ov):
            df["session_strength_min"] = base
            return df

        if "timestamp" in df.columns:
            ts = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
        elif isinstance(df.index, pd.DatetimeIndex):
            ts = pd.to_datetime(pd.Series(np.asarray(df.index), index=df.index), utc=True, errors="coerce")
        else:
            df["session_strength_min"] = base
            return df

        m = (ts.dt.hour.fillna(0).astype(int) * 60 + ts.dt.minute.fillna(0).astype(int)) % 1440
        m_vals = m.to_numpy(dtype=int)
        eff = np.array([base if ov[mi] is None else max(base, float(ov[mi])) for mi in m_vals], dtype=float)
        eff = np.where(ts.isna().to_numpy(), base, eff)
        df["session_strength_min"] = eff

        sig = df["signal"].astype(int)
        weak = (sig != 0) & (df["signal_strength"].astype(float) < eff)
        df.loc[weak, "signal"] = 0
        df.loc[weak, "signal_strength"] = 0.0
        df.loc[weak, "confluence_count_ict"] = 0
        return df

    def _calculate_strength(self, row: pd.Series) -> float:
        if row.get("signal", 0) == 0:
            return 0.0

        strength = 0.0

        if row["signal"] == 1 and bool(row.get("bullish_sweep", False)):
            strength += 30
        elif row["signal"] == -1 and bool(row.get("bearish_sweep", False)):
            strength += 30

        if row["signal"] == 1 and bool(row.get("bullish_fvg", False)):
            strength += 25
        elif row["signal"] == -1 and bool(row.get("bearish_fvg", False)):
            strength += 25

        if row["signal"] == 1 and bool(row.get("discount", False)):
            strength += 25
        elif row["signal"] == -1 and bool(row.get("premium", False)):
            strength += 25

        ote_levels = list(getattr(self.config, "OTE_LEVELS", []) or [])
        ote_key = str(ote_levels[0]) if ote_levels else "0.62"
        ote_long = row.get(f"ote_long_{ote_key}", np.nan)
        ote_short = row.get(f"ote_short_{ote_key}", np.nan)
        if row["signal"] == 1 and pd.notna(ote_long) and float(row["close"]) > float(ote_long):
            strength += 20
        elif row["signal"] == -1 and pd.notna(ote_short) and float(row["close"]) < float(ote_short):
            strength += 20

        return float(min(strength, 100.0))

    def process_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        df = self.detect_liquidity_pools(df)
        df = self.detect_fvg(df)
        df = self.calculate_premium_discount(df)
        df = self.detect_ote(df)
        df = self.liquidity_sweep_detection(df)
        df = annotate_regime(df, self.config)
        df = self.generate_signal(df)
        return df

