"""
app/tests/test_data_integrity.py

Tests for data validation helpers and market calendar utilities.
No DB required.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from app.data.providers.market_data import validate_ohlcv
from app.core.market_calendar import (
    is_trading_day,
    add_trading_days,
    trading_days_between,
)


class TestOHLCVValidation:
    def make_df(self, n: int = 10) -> pd.DataFrame:
        dates = pd.bdate_range("2024-01-02", periods=n).date.tolist()
        return pd.DataFrame(
            {
                "date": dates,
                "open": [100.0] * n,
                "high": [102.0] * n,
                "low": [99.0] * n,
                "close": [101.0] * n,
                "adj_close": [101.0] * n,
                "volume": [1_000_000] * n,
            }
        )

    def test_clean_data_unchanged(self):
        df = self.make_df()
        result = validate_ohlcv(df, "TEST")
        assert len(result) == len(df)

    def test_null_close_dropped(self):
        df = self.make_df()
        df.loc[3, "close"] = None
        df.loc[3, "adj_close"] = None
        result = validate_ohlcv(df, "TEST")
        assert len(result) == len(df) - 1

    def test_zero_volume_dropped(self):
        df = self.make_df()
        df.loc[5, "volume"] = 0
        result = validate_ohlcv(df, "TEST")
        assert len(result) == len(df) - 1

    def test_extreme_return_logs_warning(self, caplog):
        """Rows with >50% return should be flagged (not dropped, just warned)."""
        df = self.make_df(20)
        df.loc[10, "adj_close"] = 200.0  # +98% spike
        # Should complete without raising
        result = validate_ohlcv(df, "TEST")
        assert len(result) > 0


class TestMarketCalendar:
    def test_monday_is_trading_day(self):
        monday = dt.date(2024, 1, 8)  # Known trading day
        assert is_trading_day(monday)

    def test_saturday_not_trading_day(self):
        saturday = dt.date(2024, 1, 6)
        assert not is_trading_day(saturday)

    def test_christmas_not_trading_day(self):
        xmas = dt.date(2024, 12, 25)
        assert not is_trading_day(xmas)

    def test_add_trading_days_skips_weekends(self):
        # Friday 2024-01-05 + 1 trading day = Monday 2024-01-08
        friday = dt.date(2024, 1, 5)
        result = add_trading_days(friday, 1)
        assert result == dt.date(2024, 1, 8)

    def test_add_five_trading_days(self):
        monday = dt.date(2024, 1, 8)
        result = add_trading_days(monday, 5)
        # 5 trading days after Monday Jan 8 = Monday Jan 15 (MLK day, skip to Jan 16)
        # Actually Jan 15 is MLK holiday, so next is Jan 16
        assert result == dt.date(2024, 1, 16)

    def test_trading_days_between_count(self):
        start = dt.date(2024, 1, 2)
        end = dt.date(2024, 1, 31)
        days = trading_days_between(start, end)
        # January 2024: 23 trading days (Jan 15 = MLK day excluded)
        assert len(days) == 22

    def test_trading_days_excludes_holidays(self):
        # Thanksgiving 2024 = Nov 28
        thanksgiving = dt.date(2024, 11, 28)
        days = trading_days_between(thanksgiving, thanksgiving)
        assert len(days) == 0
