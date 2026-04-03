"""
app/data/providers/benchmark_data.py
app/data/providers/sector_data.py
(combined in one file for brevity — split if preferred)

Loads SPY benchmark and sector ETF data through the same provider abstraction.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

from app.core.constants import BENCHMARK, SECTOR_ETFS
from app.core.logging import get_logger
from app.data.providers.market_data import get_provider, validate_ohlcv

logger = get_logger(__name__)


def fetch_benchmark(start: dt.date, end: dt.date) -> pd.DataFrame:
    """Fetch SPY OHLCV for the given date range."""
    provider = get_provider()
    df = provider.fetch_ohlcv(BENCHMARK, start, end)
    df = validate_ohlcv(df, BENCHMARK)
    df["ticker"] = BENCHMARK
    logger.info("Benchmark loaded", ticker=BENCHMARK, rows=len(df))
    return df


def fetch_sector_etfs(start: dt.date, end: dt.date) -> pd.DataFrame:
    """Fetch all sector ETF OHLCV and return a single concatenated DataFrame."""
    provider = get_provider()
    frames = []
    for etf in SECTOR_ETFS:
        try:
            df = provider.fetch_ohlcv(etf, start, end)
            df = validate_ohlcv(df, etf)
            if df.empty:
                logger.warning("Skipping empty sector ETF", etf=etf)
                continue
            df["ticker"] = etf
            frames.append(df)
        except Exception as exc:
            logger.warning("Failed to fetch sector ETF", etf=etf, error=str(exc))

    if not frames:
        return pd.DataFrame()

    combined = pd.concat(frames, ignore_index=True)
    logger.info("Sector ETFs loaded", count=len(frames), rows=len(combined))
    return combined


def build_market_context(
    benchmark_df: pd.DataFrame,
    sector_df: pd.DataFrame,
    date: dt.date,
    lookback: int = 50,
) -> dict:
    """
    Compute market-regime and sector-trend context as of *date*.
    Returns dict keyed by ticker and 'market_regime'.

    market_regime: +1 (SPY above 50 SMA, expanding) / 0 (flat) / -1 (below, contracting)
    sector_trend_state: +1 / 0 / -1 per sector ETF
    """
    result: dict[str, int] = {}

    # Market regime from SPY
    spy = benchmark_df[benchmark_df["date"] <= date].tail(lookback).copy()
    if len(spy) >= 20:
        sma20 = spy["adj_close"].rolling(20).mean().iloc[-1]
        last_close = spy["adj_close"].iloc[-1]
        result["market_regime"] = int(last_close > sma20) - int(last_close < sma20)
    else:
        result["market_regime"] = 0

    # Sector trend states
    for etf in SECTOR_ETFS:
        etf_df = sector_df[sector_df["ticker"] == etf]
        etf_df = etf_df[etf_df["date"] <= date].tail(lookback)
        if len(etf_df) >= 20:
            sma20 = etf_df["adj_close"].rolling(20).mean().iloc[-1]
            last = etf_df["adj_close"].iloc[-1]
            result[etf] = int(last > sma20) - int(last < sma20)
        else:
            result[etf] = 0

    return result
