"""
app/features/labeling.py

Computes forward-looking binary labels for each stock-date observation.

ANTI-LEAKAGE: Labels are computed on adj_close only.
  - continue_3d / continue_5d: did the move continue past threshold?
  - drawdown_gt_3pct_5d: did an adverse excursion exceed 3% in 5 days?
  - mean_revert_3d: did a significant reversal occur within 3 days?

All horizons are in *trading days* (not calendar days).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.core.config import get_settings

settings = get_settings()


def _continuation_label(
    adj_close: pd.Series,
    current_return: pd.Series,
    horizon: int,
    threshold_pct: float,
) -> pd.Series:
    """
    For each date t:
      - If current_return > 0: label = 1 if close[t+h] > close[t] * (1 + threshold_pct/100)
      - If current_return < 0: label = 1 if close[t+h] < close[t] * (1 - threshold_pct/100)
      - If current_return == 0: label = 0

    Labels for the last *horizon* rows are NaN (no forward data yet).
    """
    n = len(adj_close)
    labels = np.full(n, np.nan)
    thresh = threshold_pct / 100.0

    for i in range(n - horizon):
        c0 = adj_close.iloc[i]
        ch = adj_close.iloc[i + horizon]
        ret = current_return.iloc[i]

        if pd.isna(ret) or pd.isna(c0) or pd.isna(ch) or c0 == 0:
            continue

        if ret > 0:
            labels[i] = int(ch >= c0 * (1 + thresh))
        elif ret < 0:
            labels[i] = int(ch <= c0 * (1 - thresh))
        else:
            labels[i] = 0

    return pd.Series(labels, index=adj_close.index)


def _drawdown_label(
    high: pd.Series,
    low: pd.Series,
    adj_close: pd.Series,
    current_return: pd.Series,
    horizon: int,
    threshold_pct: float,
) -> pd.Series:
    """
    For each date t: label = 1 if the *adverse* move exceeds threshold_pct
    within the next *horizon* trading days.

    Adverse direction:
      - If today is up: adverse is a decline (use min(low) over window)
      - If today is down: adverse is a rally (use max(high) over window)
    """
    n = len(adj_close)
    labels = np.full(n, np.nan)
    thresh = threshold_pct / 100.0

    for i in range(n - horizon):
        c0 = adj_close.iloc[i]
        ret = current_return.iloc[i]
        if pd.isna(ret) or pd.isna(c0) or c0 == 0:
            continue

        window_low = low.iloc[i + 1 : i + horizon + 1]
        window_high = high.iloc[i + 1 : i + horizon + 1]

        if ret >= 0:
            # adverse = downside
            mae = (c0 - window_low.min()) / c0
        else:
            # adverse = upside
            mae = (window_high.max() - c0) / c0

        labels[i] = int(mae >= thresh)

    return pd.Series(labels, index=adj_close.index)


def _triple_barrier_label(
    adj_close: pd.Series,
    high: pd.Series,
    low: pd.Series,
    current_return: pd.Series,
    atr_pct: pd.Series,
    horizon: int,
    atr_mult: float,
) -> pd.Series:
    """
    Lopez de Prado–style triple barrier labeling (Lopez de Prado, 2018).

    For each date t where |current_return| >= min_move (already filtered by caller):
      - Set an upper barrier at close[t] × (1 + atr_mult × atr_pct[t])
      - Set a lower barrier at close[t] × (1 - atr_mult × atr_pct[t])
      - Scan the next *horizon* bars' high/low to find which barrier is touched first.

    For an UP day (current_return > 0):
      - Upper barrier touched first → label 1  (continuation)
      - Lower barrier touched first → label 0  (reversal / stop-out)
      - Neither touched within horizon → label 0 (no follow-through)

    For a DOWN day (current_return < 0):
      - Lower barrier touched first → label 1  (continuation)
      - Upper barrier touched first → label 0  (reversal / stop-out)
      - Neither touched within horizon → label 0 (no follow-through)

    This replaces the fixed % threshold with a stock-specific, volatility-adapted
    threshold, which is the key fix for the near-random AUC on continuation models.
    """
    n = len(adj_close)
    labels = np.full(n, np.nan)

    close_arr = adj_close.values
    high_arr = high.values
    low_arr = low.values
    ret_arr = current_return.values
    atr_arr = atr_pct.values

    for i in range(n - horizon):
        c0 = close_arr[i]
        ret = ret_arr[i]
        atr = atr_arr[i]

        if np.isnan(ret) or np.isnan(c0) or np.isnan(atr) or c0 == 0:
            continue

        barrier_width = atr_mult * atr  # fraction of close
        upper = c0 * (1.0 + barrier_width)
        lower = c0 * (1.0 - barrier_width)

        future_high = high_arr[i + 1 : i + horizon + 1]
        future_low = low_arr[i + 1 : i + horizon + 1]

        # Find the first index (0-based within window) that touches each barrier
        upper_hits = np.where(future_high >= upper)[0]
        lower_hits = np.where(future_low <= lower)[0]

        up_idx = int(upper_hits[0]) if len(upper_hits) > 0 else horizon + 1
        dn_idx = int(lower_hits[0]) if len(lower_hits) > 0 else horizon + 1

        if ret > 0:
            # Bullish day: upper = continuation target, lower = stop-out
            if up_idx <= dn_idx and up_idx <= horizon:
                labels[i] = 1
            else:
                labels[i] = 0
        else:
            # Bearish day: lower = continuation target, upper = stop-out
            if dn_idx <= up_idx and dn_idx <= horizon:
                labels[i] = 1
            else:
                labels[i] = 0

    return pd.Series(labels, index=adj_close.index)


def _mean_revert_label(
    adj_close: pd.Series,
    current_return: pd.Series,
    horizon: int,
    threshold_pct: float,
) -> pd.Series:
    """
    After an extended move (|current_return| > threshold_pct),
    label = 1 if the price reverses by at least threshold_pct in opposite
    direction within *horizon* trading days.
    """
    n = len(adj_close)
    labels = np.full(n, np.nan)
    thresh = threshold_pct / 100.0

    for i in range(n - horizon):
        c0 = adj_close.iloc[i]
        ret = current_return.iloc[i]
        if pd.isna(ret) or pd.isna(c0) or c0 == 0:
            continue

        window = adj_close.iloc[i + 1 : i + horizon + 1]

        if ret > thresh:
            # Extended up move — look for reversion down
            min_close = window.min()
            labels[i] = int((c0 - min_close) / c0 >= thresh)
        elif ret < -thresh:
            # Extended down move — look for reversion up
            max_close = window.max()
            labels[i] = int((max_close - c0) / c0 >= thresh)
        else:
            labels[i] = 0

    return pd.Series(labels, index=adj_close.index)


def compute_labels(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add all forward label columns to a sorted per-stock DataFrame.

    Required columns: date, adj_close, high, low
    The column `ret_1d` must already exist (from indicators.py).
    When settings.continuation_use_triple_barrier is True (default), continuation
    labels use ATR-adaptive triple barrier labeling instead of the fixed threshold.
    """
    df = df.sort_values("date").reset_index(drop=True)
    c = df["adj_close"]
    h = df["high"]
    l = df["low"]
    ret = df["ret_1d"]

    # Filter out low-conviction days for continuation labels only.
    # Days where |ret_1d| < min_move have no clear directional signal —
    # labeling them introduces noise that degrades continuation model quality.
    # NaN rows are excluded from training via dropna().
    min_move = settings.continuation_min_move_pct / 100.0
    ret_for_labels = ret.where(ret.abs() >= min_move)

    if settings.continuation_use_triple_barrier and "atr_pct" in df.columns:
        # Triple barrier: ATR-relative barriers adapt to each stock's volatility.
        # This is the primary fix for near-random AUC on continuation models.
        atr_pct = df["atr_pct"]
        df["continue_3d"] = _triple_barrier_label(
            c, h, l, ret_for_labels, atr_pct,
            settings.label_horizon_short,
            settings.continuation_barrier_atr_mult,
        )
        df["continue_5d"] = _triple_barrier_label(
            c, h, l, ret_for_labels, atr_pct,
            settings.label_horizon_long,
            settings.continuation_barrier_atr_mult,
        )
    else:
        # Fallback: legacy fixed-threshold labeling
        df["continue_3d"] = _continuation_label(
            c, ret_for_labels, settings.label_horizon_short, settings.continuation_threshold_pct
        )
        df["continue_5d"] = _continuation_label(
            c, ret_for_labels, settings.label_horizon_long, settings.continuation_threshold_pct
        )

    df["drawdown_gt_3pct_5d"] = _drawdown_label(
        h, l, c, ret, settings.label_horizon_long, settings.drawdown_threshold_pct
    )
    df["mean_revert_3d"] = _mean_revert_label(
        c, ret, settings.label_horizon_short, settings.mean_revert_threshold_pct
    )

    return df
