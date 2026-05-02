"""
app/core/constants.py
Static constants used across the project.
"""

# ── Sector ETF mapping ────────────────────────────────────────────────────────
SECTOR_ETF_MAP: dict[str, str] = {
    "Technology": "XLK",
    "Health Care": "XLV",
    "Financials": "XLF",
    "Consumer Discretionary": "XLY",
    "Consumer Staples": "XLP",
    "Energy": "XLE",
    "Utilities": "XLU",
    "Industrials": "XLI",
    "Materials": "XLB",
    "Real Estate": "XLRE",
    "Communication Services": "XLC",
    "Unknown": "SPY",
    # Crypto assets use SPY as a stand-in sector benchmark (no dedicated ETF with
    # full history).  The mapping keeps alpha_sector_* features valid for BTC-USD.
    "Crypto": "SPY",
}

# Exclude SPY — it maps from "Unknown" but is already the benchmark, not a sector ETF
SECTOR_ETFS = list(set(v for v in SECTOR_ETF_MAP.values() if v != "SPY"))
BENCHMARK = "SPY"

# ── Classification labels ─────────────────────────────────────────────────────
class Classification:
    # Continuation tiers — two distinct levels of confidence
    STRONG_CONTINUATION = "Strong continuation profile"       # highest bar: both horizons confirm, ADX trending, sector aligned
    FAVORABLE_SETUP = "Trend-confirming participation"        # solid setup: trend supports, drawdown risk acceptable
    WEAK_CONTINUATION = "Weak continuation setup"
    # Deceptive move patterns
    BREAKOUT_EXHAUSTION = "Breakout with exhaustion risk"
    SPECULATIVE_SPIKE = "Speculative spike"
    PANIC_FLUSH = "Panic flush with unstable structure"
    HIGH_ATTENTION_LOW_TRUST = "High attention, low trust"
    NEUTRAL = "Neutral / insufficient signal"


# ── Explanation flags ─────────────────────────────────────────────────────────
class Flag:
    REL_VOL_ELEVATED = "Relative volume elevated"
    VOL_EXPANDING = "Volatility expanding"
    GAP_DRIVEN = "Gap-driven move"
    WEAK_SECTOR = "Weak sector confirmation"
    PRICE_EXTENDED = "Price extended from trend"
    ADX_STRENGTHENING = "ADX strengthening"
    FOLLOW_THROUGH_WEAK = "Follow-through historically weak"
    POST_EVENT_INSTABILITY = "Post-event instability"
    TREND_STRENGTH_IMPROVING = "Trend strength improving"
    REL_VOL_SUPPORTIVE = "Relative volume supportive"
    SECTOR_ALIGNED = "Sector aligned"
    MEAN_REVERT_RISK = "Mean reversion risk elevated"


# ── Confidence buckets ─────────────────────────────────────────────────────────
class ConfidenceBucket:
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INSUFFICIENT = "insufficient"


# ── Risk score bands ──────────────────────────────────────────────────────────
RISK_SCORE_HIGH = 0.65
RISK_SCORE_LOW = 0.35

# ── Feature column names ──────────────────────────────────────────────────────
PRICE_FEATURES = [
    "ret_1d", "ret_3d", "ret_5d", "ret_10d",
    "gap_pct",
    "dist_sma20", "dist_sma50",
    "slope_sma20", "slope_sma50",
    "above_sma20", "above_sma50", "above_sma200",
    # Candle quality — where did price close in its range, and how committed was the body?
    "close_in_range",       # 0=closed at low, 1=closed at high; key continuation signal
    "body_to_range_ratio",  # signed: +1=full bullish body, -1=full bearish body
]

VOL_FEATURES = [
    "atr_pct",
    "rvol_5d", "rvol_10d",
    "rvol_percentile",
    "range_expansion_ratio",
    "gap_freq_10d",
]

PARTICIPATION_FEATURES = [
    "volume",
    "rel_vol_20d",
    "rel_dollar_vol",
    "vol_percentile",
    "up_vol_ratio",
    "vol_trend_3d",  # volume acceleration: recent 3d vs prior 3d; > 1 = expanding demand
]

TREND_FEATURES = [
    "adx",
    "di_spread",
    "adx_slope",
    "hh_hl_flag",
]

EXTENSION_FEATURES = [
    "move_zscore",
    "dist_mean_atr",
    "consec_days",
    "exhaustion_flag",
]

RELATIVE_FEATURES = [
    "alpha_spy_1d", "alpha_spy_3d", "alpha_spy_5d",
    "alpha_sector_1d", "alpha_sector_3d", "alpha_sector_5d",
    "sector_trend_state",
    "market_regime",
    "market_regime_hmm",  # Gaussian HMM 3-state regime (0=bear, 1=choppy, 2=bull)
    "beta_20d",           # rolling 20-day market beta; high beta reduces signal credibility
]

MOMENTUM_FEATURES = [
    "rsi_14",
    "macd_signal",
    "roc_10",
    "roc_20",
    "roc_63",             # 3-month momentum: strongest single predictor of continuation
    "price_momentum_score",
]

ALL_FEATURES = (
    PRICE_FEATURES
    + VOL_FEATURES
    + PARTICIPATION_FEATURES
    + TREND_FEATURES
    + EXTENSION_FEATURES
    + RELATIVE_FEATURES
    + MOMENTUM_FEATURES
)

# Continuation models: direction-sensitive — momentum + candle quality matter most.
# close_in_range and body_to_range_ratio (in PRICE_FEATURES) are the primary additions.
# range_expansion_ratio added: a wide-range day that closes high/low is a stronger signal.
# roc_63, beta_20d, market_regime_hmm: v2 additions from 2024–2026 research.
CONTINUATION_FEATURES = (
    PRICE_FEATURES
    + PARTICIPATION_FEATURES
    + TREND_FEATURES
    + EXTENSION_FEATURES
    + RELATIVE_FEATURES
    + MOMENTUM_FEATURES
    + ["atr_pct", "rvol_5d", "rvol_10d", "range_expansion_ratio"]  # targeted vol subset
)

# Risk models use everything
RISK_FEATURES = ALL_FEATURES

# ── Target columns ─────────────────────────────────────────────────────────────
TARGET_CONTINUE_3D = "continue_3d"
TARGET_CONTINUE_5D = "continue_5d"
TARGET_DRAWDOWN_5D = "drawdown_gt_3pct_5d"
TARGET_MEAN_REVERT_3D = "mean_revert_3d"

ALL_TARGETS = [TARGET_CONTINUE_3D, TARGET_CONTINUE_5D, TARGET_DRAWDOWN_5D, TARGET_MEAN_REVERT_3D]

# ── Directional continuation targets ──────────────────────────────────────────
# Up-day and down-day continuation are structurally different setups and benefit
# from separate models.  The predictor routes to these at inference time based on
# the sign of ret_1d, falling back to the undirected model if artifacts are missing.
TARGET_CONTINUE_3D_UP = "continue_3d_up"
TARGET_CONTINUE_3D_DN = "continue_3d_dn"
TARGET_CONTINUE_5D_UP = "continue_5d_up"
TARGET_CONTINUE_5D_DN = "continue_5d_dn"

CONTINUATION_DIRECTIONAL_TARGETS = [
    TARGET_CONTINUE_3D_UP,
    TARGET_CONTINUE_3D_DN,
    TARGET_CONTINUE_5D_UP,
    TARGET_CONTINUE_5D_DN,
]
