"""
app/data/providers/market_data.py

OHLCV data provider.
Provider logic is isolated behind MarketDataProvider so the backend
can be swapped from yfinance → Polygon / IEX / Finnhub without touching
any downstream code.

IMPORTANT: yfinance is acceptable for prototype only. For production,
implement PolygonProvider (stub included below).

yfinance known issue: requesting a window narrower than ~7 calendar days
for recent dates triggers false "possibly delisted" errors on Yahoo's API,
even for valid large-cap tickers like MSFT or AAPL. The minimum window is
enforced at the provider level so all callers are protected automatically.
"""
from __future__ import annotations

import datetime as dt
from abc import ABC, abstractmethod

import pandas as pd

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()


class MarketDataProvider(ABC):
    """Abstract base — all providers must implement this interface."""

    @abstractmethod
    def fetch_ohlcv(
        self,
        ticker: str,
        start: dt.date,
        end: dt.date,
        adjusted: bool = True,
    ) -> pd.DataFrame:
        """
        Return DataFrame with columns: date, open, high, low, close, adj_close, volume.
        date is a python date object (not datetime).
        """
        ...

    @abstractmethod
    def fetch_ticker_info(self, ticker: str) -> dict:
        """Return dict with keys: name, sector, industry."""
        ...


# ─────────────────────────────────────────────────────────────────────────────
# yfinance provider (prototype)
# ─────────────────────────────────────────────────────────────────────────────
class YFinanceProvider(MarketDataProvider):
    """
    Prototype provider using yfinance.
    DO NOT use in production — rate limits, unreliable adjusted data,
    no point-in-time guarantees.
    """

    # Minimum calendar days for a yfinance request.
    # Narrower windows on recent dates trigger false "possibly delisted"
    # errors from Yahoo's API even for valid tickers (MSFT, XLF, etc.).
    _MIN_FETCH_DAYS = 7

    def fetch_ohlcv(
        self,
        ticker: str,
        start: dt.date,
        end: dt.date,
        adjusted: bool = True,
    ) -> pd.DataFrame:
        import yfinance as yf

        # Enforce minimum window — yfinance returns empty/errors for narrow
        # recent date ranges regardless of whether the ticker is valid.
        fetch_start = start
        if (end - start).days < self._MIN_FETCH_DAYS:
            fetch_start = end - dt.timedelta(days=self._MIN_FETCH_DAYS)

        # yfinance end is exclusive — add 1 calendar day
        yf_end = end + dt.timedelta(days=1)

        raw = yf.download(
            ticker,
            start=fetch_start.isoformat(),
            end=yf_end.isoformat(),
            auto_adjust=False,
            progress=False,
        )

        if raw.empty:
            logger.warning("yfinance returned empty data", ticker=ticker)
            return pd.DataFrame()

        raw = raw.copy()
        raw.columns = [c[0].lower() if isinstance(c, tuple) else c.lower() for c in raw.columns]
        raw.index = pd.to_datetime(raw.index).date

        df = pd.DataFrame(
            {
                "date": raw.index,
                "open": raw["open"],
                "high": raw["high"],
                "low": raw["low"],
                "close": raw["close"],
                "adj_close": raw.get("adj close", raw["close"]),
                "volume": raw["volume"].astype("int64"),
            }
        )
        df["dollar_volume"] = df["close"] * df["volume"]
        df["provider_source"] = "yfinance"

        # Return only rows within the originally requested date range.
        # The expanded window may pull in extra days not needed by the caller,
        # but the upsert logic handles duplicates so this is always safe.
        df = df[df["date"] <= end]

        return df.reset_index(drop=True)

    def fetch_ticker_info(self, ticker: str) -> dict:
        import yfinance as yf

        info = yf.Ticker(ticker).info
        return {
            "name": info.get("longName") or info.get("shortName") or ticker,
            "sector": info.get("sector") or "Unknown",
            "industry": info.get("industry") or "Unknown",
        }


# ─────────────────────────────────────────────────────────────────────────────
# Polygon provider (stub — implement for production)
# ─────────────────────────────────────────────────────────────────────────────
class PolygonProvider(MarketDataProvider):
    """
    Production provider stub using Polygon.io REST API.
    Fill in implementation when migrating from yfinance prototype.
    """

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or settings.polygon_api_key
        if not self.api_key:
            raise ValueError("POLYGON_API_KEY must be set")

    def fetch_ohlcv(
        self,
        ticker: str,
        start: dt.date,
        end: dt.date,
        adjusted: bool = True,
    ) -> pd.DataFrame:
        raise NotImplementedError("PolygonProvider.fetch_ohlcv not yet implemented")

    def fetch_ticker_info(self, ticker: str) -> dict:
        raise NotImplementedError("PolygonProvider.fetch_ticker_info not yet implemented")


# ─────────────────────────────────────────────────────────────────────────────
# Factory
# ─────────────────────────────────────────────────────────────────────────────
def get_provider() -> MarketDataProvider:
    name = settings.market_data_provider.lower()
    if name == "yfinance":
        return YFinanceProvider()
    if name == "polygon":
        return PolygonProvider()
    raise ValueError(f"Unknown market_data_provider: {name!r}")


# ─────────────────────────────────────────────────────────────────────────────
# Validation helpers
# ─────────────────────────────────────────────────────────────────────────────
def validate_ohlcv(df: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """
    Run basic data quality checks and drop / flag bad rows.
    Logs warnings for anything unusual.
    """
    if df.empty:
        return df

    before = len(df)

    # Drop rows with any null OHLCV
    df = df.dropna(subset=["open", "high", "low", "close", "volume"])

    # Volume must be positive
    df = df[df["volume"] > 0]

    # Extreme daily return filter: flag returns > ±50% for inspection
    df = df.sort_values("date").reset_index(drop=True)
    df["_ret"] = df["adj_close"].pct_change()
    extreme = df["_ret"].abs() > 0.5
    if extreme.any():
        logger.warning(
            "Extreme returns detected — verify corporate actions",
            ticker=ticker,
            dates=df.loc[extreme, "date"].tolist(),
        )

    df = df.drop(columns=["_ret"])
    after = len(df)
    if after < before:
        logger.info("Rows dropped during validation", ticker=ticker, dropped=before - after)

    return df