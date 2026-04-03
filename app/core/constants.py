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
}

# Exclude SPY — it maps from "Unknown" but is already the benchmark, not a sector ETF
SECTOR_ETFS = list(set(v for v in SECTOR_ETF_MAP.values() if v != "SPY"))
BENCHMARK = "SPY"

# ── Classification labels ─────────────────────────────────────────────────────
class Classification:
    FAVORABLE_SETUP = "Favorable setup"
    WEAK_CONTINUATION = "Weak continuation setup"
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
]

MOMENTUM_FEATURES = [
    "rsi_14",
    "macd_signal",
    "roc_10",
    "roc_20",
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

# Continuation models: direction-sensitive — momentum matters more than full vol suite
CONTINUATION_FEATURES = (
    PRICE_FEATURES
    + PARTICIPATION_FEATURES
    + TREND_FEATURES
    + EXTENSION_FEATURES
    + RELATIVE_FEATURES
    + MOMENTUM_FEATURES
    + ["atr_pct", "rvol_5d", "rvol_10d"]  # targeted vol subset only
)

# Risk models use everything
RISK_FEATURES = ALL_FEATURES

# ── Target columns ─────────────────────────────────────────────────────────────
TARGET_CONTINUE_3D = "continue_3d"
TARGET_CONTINUE_5D = "continue_5d"
TARGET_DRAWDOWN_5D = "drawdown_gt_3pct_5d"
TARGET_MEAN_REVERT_3D = "mean_revert_3d"

ALL_TARGETS = [TARGET_CONTINUE_3D, TARGET_CONTINUE_5D, TARGET_DRAWDOWN_5D, TARGET_MEAN_REVERT_3D]
