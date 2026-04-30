"""
app/core/market_calendar.py

Exchange trading calendar utilities.
Uses pandas-market-calendars so forward horizons count *trading sessions*,
not calendar days — critical for correct label generation and prediction timing.
"""
from __future__ import annotations

import datetime as dt
from functools import lru_cache

import pandas as pd
import pandas_market_calendars as mcal


@lru_cache(maxsize=8)
def _nyse() -> mcal.MarketCalendar:
    return mcal.get_calendar("NYSE")


def trading_days_between(start: dt.date, end: dt.date) -> pd.DatetimeIndex:
    """Return sorted DatetimeIndex of NYSE trading sessions in [start, end]."""
    cal = _nyse()
    schedule = cal.schedule(
        start_date=start.isoformat(), end_date=end.isoformat()
    )
    # Guard against empty schedule (e.g. a single holiday date) — newer
    # pandas_market_calendars raises AttributeError on .dt inside date_range
    # when the schedule DataFrame is empty.
    if schedule.empty:
        return pd.DatetimeIndex([])
    return mcal.date_range(schedule, frequency="1D").normalize()


def is_trading_day(date: dt.date) -> bool:
    cal = _nyse()
    schedule = cal.schedule(
        start_date=date.isoformat(), end_date=date.isoformat()
    )
    return not schedule.empty


def add_trading_days(date: dt.date, n: int) -> dt.date:
    """Return the date that is *n* trading sessions after *date* (exclusive)."""
    end_search = date + dt.timedelta(days=n * 2 + 30)
    days = trading_days_between(date, end_search)
    # Filter to days strictly after *date*
    future = days[days.date > date]  # type: ignore[attr-defined]
    if len(future) < n:
        raise ValueError(f"Could not find {n} trading days after {date}")
    return future[n - 1].date()


def prev_trading_day(date: dt.date) -> dt.date:
    """Return the most recent trading day strictly before *date*."""
    search_start = date - dt.timedelta(days=14)
    days = trading_days_between(search_start, date)
    before = days[days.date < date]  # type: ignore[attr-defined]
    if before.empty:
        raise ValueError(f"No trading day found before {date}")
    return before[-1].date()


def resolve_to_trading_day(date: dt.date) -> dt.date:
    """Return *date* if it is a trading day, otherwise the most recent trading day before it."""
    if is_trading_day(date):
        return date
    return prev_trading_day(date)


def last_closed_trading_day() -> dt.date:
    """
    Return the most recent trading day whose regular session has closed.
    Uses NYSE close (16:00 ET). After 16:00 ET today returns today,
    otherwise returns the previous trading session.
    """
    import pytz

    eastern = pytz.timezone("America/New_York")
    now_et = dt.datetime.now(eastern)
    today = now_et.date()
    close_time = dt.time(16, 0)

    if is_trading_day(today) and now_et.time() >= close_time:
        return today
    return prev_trading_day(today)
