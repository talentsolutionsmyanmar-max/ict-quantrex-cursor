import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # Trading Mode
    MODE = "BACKTEST"  # BACKTEST | PAPER | LIVE

    # Data Sources
    BINANCE_API = "https://api.binance.com/api/v3"
    TIMEFRAME = "15m"
    SYMBOL = "BTCUSDT"
    # Paper / multi-symbol: set on instance by strategy/spec.yaml market.watchlist (None = use SYMBOL only)
    WATCHLIST = None

    # ICT Parameters
    ICT_RANGE_HOURS = 5
    LIQUIDITY_BUFFER = 0.006
    FVG_THRESHOLD = 0.001
    OTE_LEVELS = [0.62, 0.705, 0.79]
    # v2 spec (ict.fvg.*) — adaptive uses ATR-normalized gap; static keeps % of close
    FVG_METHOD = "static"
    FVG_MIN_GAP_ATR = 0.3
    FVG_CONFIRMATION_CANDLES = 0
    FVG_MITIGATION_FILTER = False
    FVG_IGNORE_MITIGATED = False

    # v2 market.allocation — max open symbols in paper (None = unlimited)
    ALLOCATION_METHOD = "equal"
    MAX_CONCURRENT_POSITIONS = None
    CORRELATION_CAP = 0.7
    CORRELATION_REDUCE_THRESHOLD = 0.85

    # v2 ict.liquidity — 0 disables volume spike filter on sweeps
    SWEEP_VOLUME_SPIKE_FACTOR = 0.0

    # execution.* from spec (paper friction toggles)
    MODEL_PARTIAL_FILLS = True
    LOG_EVERY_FILL = False

    # === RISK MANAGEMENT (FIXED) ===
    INITIAL_CAPITAL = 10000
    RISK_PER_TRADE = 0.01  # 1.0% of capital at risk per trade
    SIZING_METHOD = "fixed_risk"  # fixed_risk | volatility_targeting
    VOLATILITY_TARGET_ANNUAL = 0.15
    ATR_MULTIPLIER = 1.8  # Stop = 1.8x ATR
    MIN_CONFLUENCE = 2  # Min ICT factors for entry
    MIN_SIGNAL_STRENGTH = 68  # Min signal strength

    # Profit Taking (Scale-Out)
    TP1_RATIO = 1.0  # 50% at 1:1 R:R
    TP2_RATIO = 2.0  # 30% at 2:1 R:R
    TP3_RATIO = 3.0  # 20% at 3:1 R:R (runner)
    TP1_PCT = 0.50  # 50% position
    TP2_PCT = 0.30  # 30% position
    TP3_PCT = 0.20  # 20% position

    # Trailing Stop
    TRAIL_AFTER_TP1 = True  # Enable trail after TP1 hit
    TRAIL_ATR_MULTIPLIER = 1.0  # Trail distance = 1x ATR

    # Time Exit
    MAX_CANDLES_HOLD = 48  # Max 48 candles (12 hours on 15m)

    # Regime gating (v1.9 staged; keep disabled until validated)
    REGIME_GATE_ENABLED = False
    REGIME_ADX_PERIOD = 14
    REGIME_ADX_MIN = 18.0
    REGIME_ATR_PERIOD = 14
    REGIME_ATR_PCT_MIN = 0.35
    REGIME_EMA_FAST = 20
    REGIME_EMA_SLOW = 50
    REGIME_PERSIST_BARS = 3
    REGIME_RANGE_MIN_SIGNAL_STRENGTH = 75
    REGIME_RANGE_MIN_CONFLUENCE = 3

    # Backtest Settings
    BACKTEST_START_DATE = "2024-01-01"
    BACKTEST_END_DATE = "2024-12-31"
    COMMISSION = 0.001
    SLIPPAGE = 0.0005

    # Portfolio constraints (unused for now)
    MAX_DAILY_LOSS = 0.03
    MAX_DRAWDOWN = 0.10
    MAX_POSITION_NOTIONAL_USD = 50000.0

    # Paper / live poll interval (seconds); align with timeframe in production if needed
    POLL_INTERVAL_SEC = 15

    # Database
    DATABASE = "trading.db"

    # API Keys (for paper/live)
    BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "")
    BINANCE_SECRET = os.getenv("BINANCE_SECRET", "")
    # USDT-M futures public API (funding / premiumIndex only; no keys required)
    BINANCE_FUTURES_API = os.getenv("BINANCE_FUTURES_API", "https://fapi.binance.com/fapi/v1")

    # Unusual Whales (US equities / options flow — optional; see unusual_whales_client.py)
    UNUSUAL_WHALES_API_KEY = os.getenv("UNUSUAL_WHALES_API_KEY", "")
    UW_CLIENT_API_ID = os.getenv("UW_CLIENT_API_ID", "100001")


    @classmethod
    def from_dict(cls, d: dict) -> "Config":
        """Create a Config instance with overrides from a dictionary.
        Only sets attributes that already exist on the class.
        """
        cfg = cls()
        for k, v in d.items():
            if hasattr(cls, k):
                setattr(cfg, k, v)
        return cfg


def build_config() -> "Config":
    """Load strategy/spec.yaml over defaults; call on process start and when cloning for backtests."""
    cfg = Config()
    try:
        from strategy.load_spec import apply_spec_to_config

        apply_spec_to_config(cfg)
    except Exception as e:
        print(f"Strategy spec warning (using class defaults): {e}")
    return cfg

