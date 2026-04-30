"""
app/features/regime_detection.py

Computes a market regime label for every trading day using SPY daily returns.

Primary method: Gaussian HMM (hmmlearn) with 3 hidden states, refit every
regime_refit_days trading sessions.  States are relabelled so that
  0 = bear  (lowest mean log-return)
  1 = choppy (middle mean log-return)
  2 = bull   (highest mean log-return)
regardless of random HMM initialisation order.

Fallback: if hmmlearn is not installed (or fitting fails), a simple
rule-based regime is returned instead:
  bull  (2): SPY above its 50-SMA *and* 63-day ROC > +5 %
  bear  (0): SPY below its 50-SMA *and* 63-day ROC < -5 %
  choppy(1): everything else

The series is indexed by date and contains integer {0, 1, 2}.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.core.config import get_settings

settings = get_settings()

try:
    from hmmlearn import hmm as _hmmlearn_hmm  # type: ignore
    _HMMLEARN_AVAILABLE = True
except ImportError:
    _HMMLEARN_AVAILABLE = False


# ─────────────────────────────────────────────────────────────────────────────
# Rule-based fallback
# ─────────────────────────────────────────────────────────────────────────────

def _rules_regime(spy_close: pd.Series) -> pd.Series:
    """
    Simple rule-based regime using SPY 50-SMA and 63-day ROC.
    Returns a Series with integer values 0/1/2 aligned to spy_close.index.
    """
    sma50 = spy_close.rolling(50, min_periods=20).mean()
    roc63 = spy_close.pct_change(63)

    above_sma = spy_close > sma50
    bull = (above_sma & (roc63 > 0.05)).astype(int) * 2
    bear = (~above_sma & (roc63 < -0.05)).astype(int) * 0

    regime = pd.Series(1, index=spy_close.index)  # default: choppy
    regime[above_sma & (roc63 > 0.05)] = 2   # bull
    regime[~above_sma & (roc63 < -0.05)] = 0  # bear
    return regime.astype(int)


# ─────────────────────────────────────────────────────────────────────────────
# HMM regime
# ─────────────────────────────────────────────────────────────────────────────

def _fit_hmm(log_returns: np.ndarray, n_states: int) -> "_hmmlearn_hmm.GaussianHMM":
    """Fit a Gaussian HMM on *log_returns* (1-D numpy array)."""
    model = _hmmlearn_hmm.GaussianHMM(
        n_components=n_states,
        covariance_type="full",
        n_iter=200,
        random_state=42,
    )
    model.fit(log_returns.reshape(-1, 1))
    return model


def _sort_states_by_mean(model: "_hmmlearn_hmm.GaussianHMM") -> np.ndarray:
    """
    Return a mapping array so that states are relabelled by ascending mean.
    sorted_label[original_state] = new_label  (0=lowest, n-1=highest mean)
    """
    means = model.means_.flatten()
    order = np.argsort(means)          # original state indices sorted by mean
    mapping = np.empty(len(order), dtype=int)
    for new_label, orig in enumerate(order):
        mapping[orig] = new_label
    return mapping


def _hmm_regime(
    spy_close: pd.Series,
    n_states: int,
    refit_days: int,
) -> pd.Series:
    """
    Compute per-date regime labels using rolling Gaussian HMM.

    Strategy:
      - Require at least 252 days of history before first fit.
      - Refit every *refit_days* trading sessions using all history up to that point.
      - Between refits, apply the last fitted model online (decode on expanding window).
      - States sorted by mean log-return so 0=bear, 1=choppy, 2=bull.

    Returns a pd.Series of int, indexed like spy_close, with NaN where
    insufficient history existed.
    """
    log_ret = np.log(spy_close / spy_close.shift(1)).fillna(0).values
    n = len(log_ret)
    min_fit = 252
    regime = np.full(n, np.nan)

    current_model: "_hmmlearn_hmm.GaussianHMM | None" = None
    state_map: np.ndarray | None = None
    last_fit_idx: int = -refit_days  # force fit on first eligible bar

    for i in range(min_fit, n):
        # Refit every refit_days bars
        if (i - last_fit_idx) >= refit_days:
            try:
                model = _fit_hmm(log_ret[:i], n_states)
                state_map = _sort_states_by_mean(model)
                current_model = model
                last_fit_idx = i
            except Exception:
                pass  # keep using previous model

        if current_model is None or state_map is None:
            continue

        # Decode the full history up to i to get latest state for day i
        try:
            raw_states = current_model.predict(log_ret[:i + 1].reshape(-1, 1))
            regime[i] = int(state_map[raw_states[-1]])
        except Exception:
            pass

    return pd.Series(regime, index=spy_close.index)


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def compute_spy_regime(spy_df: pd.DataFrame) -> pd.Series:
    """
    Given a SPY price DataFrame with columns [date, adj_close], return a
    pd.Series indexed by *date* (as date objects) with integer regime labels:
      0 = bear, 1 = choppy, 2 = bull

    Uses Gaussian HMM if hmmlearn is installed, otherwise falls back to
    SMA/ROC rules.  NaN rows (insufficient history) are forward-filled then
    back-filled so every date gets a label.
    """
    spy = spy_df[["date", "adj_close"]].sort_values("date").copy()
    spy = spy.drop_duplicates("date")
    close = spy.set_index("date")["adj_close"]

    if _HMMLEARN_AVAILABLE:
        try:
            regime_series = _hmm_regime(
                close,
                n_states=settings.regime_n_states,
                refit_days=settings.regime_refit_days,
            )
        except Exception:
            regime_series = _rules_regime(close)
    else:
        regime_series = _rules_regime(close)

    # Fill early NaN rows: use first valid value backward, then forward
    regime_series = regime_series.ffill().bfill().astype(int)
    regime_series.index = pd.to_datetime(regime_series.index)
    return regime_series
