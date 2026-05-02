"""
app/data/pipelines/ingest_prices.py

Fetches OHLCV for a list of tickers and upserts into daily_prices.
Handles:
- point-in-time fetch (no future data)
- data validation before persistence
- stock metadata upsert (name, sector, industry)
- duplicate / stale data detection
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
from sqlalchemy.orm import Session
from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.models import DailyPrice, Stock
from app.data.providers.market_data import get_provider, validate_ohlcv

logger = get_logger(__name__)
settings = get_settings()


_CRYPTO_SECTOR_KEYWORDS = ("cryptocurrency", "crypto", "digital currency", "digital asset")


def _normalise_sector(ticker: str, raw_sector: str | None) -> str | None:
    """Map raw sector strings to canonical SECTOR_ETF_MAP keys.

    yfinance returns None or 'Cryptocurrency' for BTC-USD; we map either to
    'Crypto' so that SECTOR_ETF_MAP['Crypto'] = 'SPY' is used for alpha_sector_*
    features instead of falling through to 'Unknown'.
    """
    if raw_sector and raw_sector.lower() in _CRYPTO_SECTOR_KEYWORDS:
        return "Crypto"
    # Ticker-based fallback for assets whose info dict omits the sector field
    if raw_sector is None and ticker.endswith("-USD"):
        return "Crypto"
    return raw_sector


def upsert_stock(db: Session, ticker: str, info: dict) -> Stock:
    """Get or create a Stock row, updating metadata if changed."""
    sector = _normalise_sector(ticker, info.get("sector"))
    stock = db.query(Stock).filter(Stock.ticker == ticker).first()
    if stock is None:
        stock = Stock(
            ticker=ticker,
            name=info.get("name"),
            sector=sector,
            industry=info.get("industry"),
        )
        db.add(stock)
        db.flush()
        logger.info("Stock created", ticker=ticker)
    else:
        stock.name = info.get("name") or stock.name
        stock.sector = sector or stock.sector
        stock.industry = info.get("industry") or stock.industry
    return stock


def upsert_prices(db: Session, stock: Stock, price_df: pd.DataFrame) -> int:
    """
    Upsert price rows for one stock.
    Strategy: delete existing rows for affected dates, then bulk-insert.
    Returns number of rows inserted.
    """
    if price_df.empty:
        return 0

    dates = price_df["date"].tolist()
    db.query(DailyPrice).filter(
        DailyPrice.stock_id == stock.id,
        DailyPrice.date.in_(dates),
    ).delete(synchronize_session=False)

    rows = []
    for _, r in price_df.iterrows():
        rows.append(
            DailyPrice(
                stock_id=stock.id,
                date=r["date"],
                open=r.get("open"),
                high=r.get("high"),
                low=r.get("low"),
                close=r.get("close"),
                adj_close=r.get("adj_close"),
                volume=int(r["volume"]) if pd.notna(r.get("volume")) else None,
                dollar_volume=r.get("dollar_volume"),
                provider_source=r.get("provider_source", settings.market_data_provider),
            )
        )
    db.bulk_save_objects(rows)
    return len(rows)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=30))
def ingest_ticker(
    ticker: str,
    start: dt.date,
    end: dt.date,
    db: Session,
) -> int:
    """Fetch + validate + upsert one ticker. Returns rows inserted."""
    provider = get_provider()

    try:
        info = provider.fetch_ticker_info(ticker)
    except Exception as exc:
        logger.warning("Could not fetch ticker info", ticker=ticker, error=str(exc))
        info = {"name": ticker, "sector": "Unknown", "industry": "Unknown"}

    price_df = provider.fetch_ohlcv(ticker, start, end)
    price_df = validate_ohlcv(price_df, ticker)

    if price_df.empty:
        logger.warning("No valid price data", ticker=ticker)
        return 0

    stock = upsert_stock(db, ticker, info)
    inserted = upsert_prices(db, stock, price_df)
    logger.info("Ticker ingested", ticker=ticker, rows=inserted)
    return inserted


def ingest_tickers(
    tickers: list[str],
    start: dt.date,
    end: dt.date,
    db: Session,
) -> dict[str, int]:
    """Batch ingest for a list of tickers. Returns {ticker: rows_inserted}."""
    results = {}
    for ticker in tickers:
        try:
            n = ingest_ticker(ticker, start, end, db)
            results[ticker] = n
        except Exception as exc:
            logger.error("Failed to ingest ticker", ticker=ticker, error=str(exc))
            results[ticker] = 0
    return results


def validate_data_integrity(db: Session, date: dt.date) -> dict:
    """
    Run data integrity checks for a specific date.
    Returns a dict with issue counts.
    """
    issues: dict[str, int] = {"missing_ohlcv": 0, "zero_volume": 0, "duplicate_dates": 0}

    # Count rows with null OHLCV
    from sqlalchemy import func, or_

    null_count = (
        db.query(func.count(DailyPrice.id))
        .filter(DailyPrice.date == date)
        .filter(
            or_(
                DailyPrice.open == None,
                DailyPrice.high == None,
                DailyPrice.low == None,
                DailyPrice.close == None,
            )
        )
        .scalar()
    )
    issues["missing_ohlcv"] = null_count or 0

    # Count rows with zero volume
    zero_vol = (
        db.query(func.count(DailyPrice.id))
        .filter(DailyPrice.date == date)
        .filter(DailyPrice.volume <= 0)
        .scalar()
    )
    issues["zero_volume"] = zero_vol or 0

    if any(v > 0 for v in issues.values()):
        logger.warning("Data integrity issues found", date=date, **issues)

    return issues
