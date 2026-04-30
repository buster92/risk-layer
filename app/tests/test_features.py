"""
app/tests/test_features.py

Unit tests for the feature engineering pipeline.
Runs without a database connection using synthetic price data.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.features.indicators import (
    add_extension,
    add_participation,
    add_price_structure,
    add_trend_strength,
    add_volatility,
    compute_all_indicators,
)
from app.core.constants import ALL_FEATURES


# ── Fixtures ───────────────────────────────────────────────────────────────────

def make_synthetic_ohlcv(n: int = 400, seed: int = 42) -> pd.DataFrame:
    """Generate synthetic OHLCV data with realistic structure."""
    rng = np.random.default_rng(seed)
    import datetime as dt

    dates = pd.bdate_range("2020-01-02", periods=n).date.tolist()
    close = 100.0
    closes, opens, highs, lows, volumes = [], [], [], [], []

    for _ in range(n):
        ret = rng.normal(0.0003, 0.015)
        open_ = close * (1 + rng.normal(0, 0.003))
        close = close * (1 + ret)
        h = max(open_, close) * (1 + abs(rng.normal(0, 0.005)))
        l = min(open_, close) * (1 - abs(rng.normal(0, 0.005)))
        vol = int(rng.integers(500_000, 5_000_000))
        closes.append(close)
        opens.append(open_)
        highs.append(h)
        lows.append(l)
        volumes.append(vol)

    return pd.DataFrame(
        {
            "date": dates,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "adj_close": closes,
            "volume": volumes,
        }
    )


@pytest.fixture
def price_df():
    return make_synthetic_ohlcv(400)


# ── Tests ──────────────────────────────────────────────────────────────────────

class TestPriceStructure:
    def test_returns_computed(self, price_df):
        df = add_price_structure(price_df.copy())
        assert "ret_1d" in df.columns
        assert "ret_5d" in df.columns
        assert "gap_pct" in df.columns
        assert "dist_sma20" in df.columns
        assert "above_sma200" in df.columns

    def test_sma_flags_binary(self, price_df):
        df = add_price_structure(price_df.copy())
        valid = df["above_sma20"].dropna()
        assert set(valid.unique()).issubset({0, 1})

    def test_no_future_leakage(self, price_df):
        """ret_1d at row i must depend only on rows ≤ i."""
        df = add_price_structure(price_df.copy())
        # First row must be NaN (no prior close)
        assert pd.isna(df["ret_1d"].iloc[0])


class TestVolatility:
    def test_atr_positive(self, price_df):
        df = add_price_structure(price_df.copy())
        df = add_volatility(df)
        valid = df["atr_pct"].dropna()
        assert (valid > 0).all()

    def test_rvol_percentile_bounded(self, price_df):
        df = add_price_structure(price_df.copy())
        df = add_volatility(df)
        valid = df["rvol_percentile"].dropna()
        assert (valid >= 0).all() and (valid <= 1).all()

    def test_range_expansion_positive(self, price_df):
        df = add_price_structure(price_df.copy())
        df = add_volatility(df)
        valid = df["range_expansion_ratio"].dropna()
        assert (valid > 0).all()


class TestParticipation:
    def test_rel_vol_positive(self, price_df):
        df = add_price_structure(price_df.copy())
        df = add_volatility(df)
        df = add_participation(df)
        valid = df["rel_vol_20d"].dropna()
        assert (valid > 0).all()

    def test_up_vol_ratio_bounded(self, price_df):
        df = add_price_structure(price_df.copy())
        df = add_volatility(df)
        df = add_participation(df)
        valid = df["up_vol_ratio"].dropna()
        assert (valid >= 0).all() and (valid <= 1).all()


class TestTrendStrength:
    def test_adx_positive(self, price_df):
        df = add_price_structure(price_df.copy())
        df = add_volatility(df)
        df = add_participation(df)
        df = add_trend_strength(df)
        valid = df["adx"].dropna()
        assert (valid >= 0).all()

    def test_hh_hl_flag_bounded(self, price_df):
        df = add_price_structure(price_df.copy())
        df = add_volatility(df)
        df = add_participation(df)
        df = add_trend_strength(df)
        valid = df["hh_hl_flag"].dropna()
        assert set(valid.unique()).issubset({0.0, 0.5, 1.0})


class TestExtension:
    def test_exhaustion_flag_binary(self, price_df):
        df = compute_all_indicators(price_df.copy())
        valid = df["exhaustion_flag"].dropna()
        assert set(valid.unique()).issubset({0, 1})

    def test_consec_days_computed(self, price_df):
        df = compute_all_indicators(price_df.copy())
        assert "consec_days" in df.columns
        assert df["consec_days"].notna().any()


class TestAllFeatures:
    def test_all_feature_columns_present(self, price_df):
        df = compute_all_indicators(price_df.copy())
        # These columns are added by add_relative_context(), not compute_all_indicators()
        relative_context_cols = {
            "alpha_spy_1d", "alpha_spy_3d", "alpha_spy_5d",
            "alpha_sector_1d", "alpha_sector_3d", "alpha_sector_5d",
            "sector_trend_state", "market_regime",
            "market_regime_hmm",  # HMM regime — needs SPY data
            "beta_20d",           # rolling beta — needs SPY data
        }
        missing = [f for f in ALL_FEATURES if f not in df.columns
                   and f not in relative_context_cols]
        assert missing == [], f"Missing feature columns: {missing}"

    def test_no_all_nan_columns(self, price_df):
        df = compute_all_indicators(price_df.copy())
        local_features = [
            "ret_1d", "adx", "rel_vol_20d", "atr_pct",
            "move_zscore", "exhaustion_flag", "consec_days",
        ]
        for col in local_features:
            assert df[col].notna().any(), f"Column {col} is all NaN"
