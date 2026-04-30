"""
app/features/indicators.py

Computes all technical indicators for a single stock's OHLCV series.
Input: DataFrame sorted by date with columns [date, open, high, low, close, adj_close, volume]
Output: DataFrame with all engineered feature columns appended.

Design principle: every feature is explainable and maps directly to a flag.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.core.config import get_settings

settings = get_settings()


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _sma(series: pd.Series, n: int) -> pd.Series:
    return series.rolling(n, min_periods=n).mean()


def _slope(series: pd.Series, n: int = 5) -> pd.Series:
    """Linear regression slope over last n periods (normalized by mean)."""
    def _ls(arr: np.ndarray) -> float:
        if np.isnan(arr).any():
            return np.nan
        x = np.arange(len(arr), dtype=float)
        slope, _ = np.polyfit(x, arr, 1)
        mean = arr.mean()
        return slope / mean if mean != 0 else 0.0

    return series.rolling(n, min_periods=n).apply(_ls, raw=True)


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(span=period, min_periods=period, adjust=False).mean()


def _adx(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Returns (ADX, +DI, -DI)."""
    up = high.diff()
    down = -low.diff()
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)

    atr = _atr(high, low, close, period)
    plus_di = 100 * pd.Series(plus_dm, index=close.index).ewm(
        span=period, min_periods=period, adjust=False
    ).mean() / atr.replace(0, np.nan)
    minus_di = 100 * pd.Series(minus_dm, index=close.index).ewm(
        span=period, min_periods=period, adjust=False
    ).mean() / atr.replace(0, np.nan)

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = dx.ewm(span=period, min_periods=period, adjust=False).mean()
    return adx, plus_di, minus_di


# ─────────────────────────────────────────────────────────────────────────────
# Price structure features
# ─────────────────────────────────────────────────────────────────────────────

def add_price_structure(df: pd.DataFrame) -> pd.DataFrame:
    c = df["adj_close"]
    o = df["open"]
    h = df["high"]
    l = df["low"]
    close_raw = df["close"]  # unadjusted, for intraday candle measures

    df["ret_1d"] = c.pct_change(1)
    df["ret_3d"] = c.pct_change(3)
    df["ret_5d"] = c.pct_change(5)
    df["ret_10d"] = c.pct_change(10)

    # Gap = today's open vs yesterday's close
    df["gap_pct"] = (o / c.shift(1) - 1)

    sma20 = _sma(c, 20)
    sma50 = _sma(c, 50)
    sma200 = _sma(c, 200)

    df["dist_sma20"] = (c - sma20) / sma20
    df["dist_sma50"] = (c - sma50) / sma50
    df["slope_sma20"] = _slope(sma20, 5)
    df["slope_sma50"] = _slope(sma50, 10)
    df["above_sma20"] = (c > sma20).astype(int)
    df["above_sma50"] = (c > sma50).astype(int)
    df["above_sma200"] = (c > sma200).astype(int)

    # Candle quality features — critical for continuation confidence
    # close_in_range: 0 = closed at low, 1 = closed at high.
    #   A strong up day closing in the top 80%+ of range shows bullish commitment.
    #   An up day closing near the low (< 0.3) is bearish — sellers took over intraday.
    range_hl = (h - l).replace(0, np.nan)
    df["close_in_range"] = (close_raw - l) / range_hl

    # body_to_range_ratio: signed candle body as fraction of total range.
    #   Positive = closed above open (bullish candle), negative = closed below open.
    #   Values near +1 / -1 indicate strong directional commitment.
    df["body_to_range_ratio"] = (close_raw - o) / range_hl

    return df


# ─────────────────────────────────────────────────────────────────────────────
# Volatility and instability features
# ─────────────────────────────────────────────────────────────────────────────

def add_volatility(df: pd.DataFrame) -> pd.DataFrame:
    c = df["adj_close"]
    h, l = df["high"], df["low"]

    atr = _atr(h, l, c, settings.atr_period)
    df["atr_pct"] = atr / c

    log_ret = np.log(c / c.shift(1))
    df["rvol_5d"] = log_ret.rolling(settings.vol_window_short).std() * np.sqrt(252)
    df["rvol_10d"] = log_ret.rolling(settings.vol_window_long).std() * np.sqrt(252)

    lookback = settings.vol_lookback_percentile
    df["rvol_percentile"] = (
        df["rvol_5d"]
        .rolling(lookback, min_periods=20)
        .apply(lambda x: pd.Series(x).rank(pct=True).iloc[-1], raw=False)
    )

    # Range expansion: today's range vs 10-day average range
    daily_range = h - l
    avg_range_10 = daily_range.rolling(10).mean()
    df["range_expansion_ratio"] = daily_range / avg_range_10.replace(0, np.nan)

    # Gap frequency in recent 10-day window
    df["gap_freq_10d"] = (
        df["gap_pct"].abs().gt(0.01).astype(int).rolling(10).sum()
    )

    return df


# ─────────────────────────────────────────────────────────────────────────────
# Participation / conviction features
# ─────────────────────────────────────────────────────────────────────────────

def add_participation(df: pd.DataFrame) -> pd.DataFrame:
    vol = df["volume"].astype(float)
    c = df["adj_close"]

    df["volume"] = vol
    rel_vol_window = settings.rel_vol_window
    avg_vol = vol.rolling(rel_vol_window, min_periods=5).mean()
    df["rel_vol_20d"] = vol / avg_vol.replace(0, np.nan)
    df["rel_dollar_vol"] = (vol * c) / (avg_vol * c).replace(0, np.nan)

    lookback = settings.vol_lookback_percentile
    df["vol_percentile"] = (
        vol.rolling(lookback, min_periods=20)
        .apply(lambda x: pd.Series(x).rank(pct=True).iloc[-1], raw=False)
    )

    # Up-day vs down-day volume over last 10 sessions
    ret = c.pct_change(1)
    up_vol = np.where(ret > 0, vol, 0.0)
    dn_vol = np.where(ret < 0, vol, 0.0)
    sum_up = pd.Series(up_vol, index=df.index).rolling(10).sum()
    sum_dn = pd.Series(dn_vol, index=df.index).rolling(10).sum()
    df["up_vol_ratio"] = sum_up / (sum_up + sum_dn).replace(0, np.nan)

    # Volume acceleration: recent 3-day total vs prior 3-day total.
    #   > 1.0 = volume expanding (demand/supply increasing) — supports continuation.
    #   < 1.0 = volume decelerating — potential exhaustion or fading interest.
    vol_sum_3 = vol.rolling(3).sum()
    vol_sum_3_lag = vol.shift(3).rolling(3).sum()
    df["vol_trend_3d"] = vol_sum_3 / vol_sum_3_lag.replace(0, np.nan)

    return df


# ─────────────────────────────────────────────────────────────────────────────
# Trend strength features
# ─────────────────────────────────────────────────────────────────────────────

def add_trend_strength(df: pd.DataFrame) -> pd.DataFrame:
    h, l, c = df["high"], df["low"], df["adj_close"]

    adx, plus_di, minus_di = _adx(h, l, c, settings.adx_period)
    df["adx"] = adx
    df["di_spread"] = plus_di - minus_di
    df["adx_slope"] = _slope(adx, 5)

    # Higher-high / higher-low flag (trend structure)
    hh = (h > h.shift(1)).astype(int)
    hl = (l > l.shift(1)).astype(int)
    df["hh_hl_flag"] = ((hh + hl) / 2)  # 0, 0.5, or 1

    return df


# ─────────────────────────────────────────────────────────────────────────────
# Extension and crowding features
# ─────────────────────────────────────────────────────────────────────────────

def add_extension(df: pd.DataFrame) -> pd.DataFrame:
    c = df["adj_close"]

    # Z-score of 1D return vs rolling 60-day return distribution
    ret = c.pct_change(1)
    roll_mean = ret.rolling(60, min_periods=20).mean()
    roll_std = ret.rolling(60, min_periods=20).std()
    df["move_zscore"] = (ret - roll_mean) / roll_std.replace(0, np.nan)

    # Distance from 20-day rolling mean in ATR units
    mean_20 = c.rolling(20).mean()
    atr = _atr(df["high"], df["low"], c, settings.atr_period)
    df["dist_mean_atr"] = (c - mean_20) / atr.replace(0, np.nan)

    # Consecutive up / down days
    direction = np.sign(ret.fillna(0))
    consec = []
    streak = 0
    for d in direction:
        if d == 0:
            streak = 0
        elif d == (streak / abs(streak) if streak != 0 else d):
            streak += int(d)
        else:
            streak = int(d)
        consec.append(streak)
    df["consec_days"] = consec

    # Exhaustion candidate: extension + volume spike + vol expansion all aligned
    df["exhaustion_flag"] = (
        (df["move_zscore"].abs() > settings.extension_zscore_threshold)
        & (df["rel_vol_20d"] > settings.high_relvol_threshold)
        & (df["range_expansion_ratio"] > 1.5)
    ).astype(int)

    return df


# ─────────────────────────────────────────────────────────────────────────────
# Momentum features
# ─────────────────────────────────────────────────────────────────────────────

def add_momentum(df: pd.DataFrame) -> pd.DataFrame:
    c = df["adj_close"]

    # RSI 14
    delta = c.diff()
    gain = delta.clip(lower=0).ewm(span=14, min_periods=14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(span=14, min_periods=14, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    df["rsi_14"] = 100 - (100 / (1 + rs))

    # MACD histogram (12/26/9)
    ema12 = c.ewm(span=12, adjust=False).mean()
    ema26 = c.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    df["macd_signal"] = macd_line - signal_line

    # Rate of change
    df["roc_10"] = c.pct_change(10)
    df["roc_20"] = c.pct_change(20)
    # 3-month momentum: primary long-horizon trend signal.
    # Research consistently shows ~63-day return as one of the strongest predictors
    # of near-term continuation, capturing the momentum premium beyond short-term noise.
    df["roc_63"] = c.pct_change(63)

    # Composite momentum score: normalized RSI deviation + ROC percentile rank
    rsi_norm = (df["rsi_14"] - 50) / 50  # -1 to +1
    roc_norm = df["roc_10"].rolling(252, min_periods=20).apply(
        lambda x: pd.Series(x).rank(pct=True).iloc[-1] - 0.5, raw=False
    )
    df["price_momentum_score"] = (rsi_norm + roc_norm) / 2

    return df


# ─────────────────────────────────────────────────────────────────────────────
# Master indicator builder
# ─────────────────────────────────────────────────────────────────────────────

def compute_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Apply all indicator groups to a sorted OHLCV DataFrame.
    The DataFrame must have columns: date, open, high, low, close, adj_close, volume.
    Returns the same DataFrame with all feature columns added.
    """
    df = df.sort_values("date").reset_index(drop=True)
    df = add_price_structure(df)
    df = add_volatility(df)
    df = add_participation(df)
    df = add_trend_strength(df)
    df = add_extension(df)
    df = add_momentum(df)
    return df
