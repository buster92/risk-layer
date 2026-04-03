"""
app/jobs/daily_ingest.py

Daily ingestion job. Runs after market close (17:00 ET).
Steps:
  1. Identify last closed trading day
  2. Ingest OHLCV for all active stocks + SPY + sector ETFs
  3. Compute and store active universe for the day
  4. Run data integrity checks

Note on fetch window: we always fetch a rolling 10-day window ending on
*date* rather than a single-day request. yfinance returns empty data for
narrow windows on recent dates (a known Yahoo Finance API limitation).
The upsert logic in ingest_prices.py handles duplicate rows safely.
"""
from __future__ import annotations

import datetime as dt

from app.core.logging import get_logger
from app.core.market_calendar import last_closed_trading_day
from app.core.constants import BENCHMARK, SECTOR_ETFS
from app.data.pipelines.ingest_prices import ingest_tickers, validate_data_integrity
from app.data.providers.active_universe import compute_active_universe, persist_universe
from app.db.session import get_db

logger = get_logger(__name__)

# Rolling window for daily ingest — wide enough to avoid yfinance narrow-range
# failures but small enough to keep ingest fast.
_DAILY_FETCH_WINDOW_DAYS = 10


def run_daily_ingest(date: dt.date | None = None) -> dict:
    """
    Run full daily ingestion for *date* (defaults to last closed trading day).
    Returns summary dict.
    """
    date = date or last_closed_trading_day()
    fetch_start = date - dt.timedelta(days=_DAILY_FETCH_WINDOW_DAYS)

    logger.info("Daily ingest starting", date=date, fetch_start=fetch_start)

    with get_db() as db:
        # Step 1: Collect all known tickers plus benchmark and sectors
        from app.db.models import Stock, DailyActiveUniverse, DailyPrice
        all_tickers = [
            s.ticker for s in db.query(Stock).filter(Stock.is_active == True).all()  # noqa: E712
        ]
        all_ingest = list(set(all_tickers + [BENCHMARK] + SECTOR_ETFS))

        # Step 2: Skip tickers that already have a price row for *date*
        already_fetched = {
            t for (t,) in db.query(Stock.ticker)
            .join(DailyPrice, DailyPrice.stock_id == Stock.id)
            .filter(DailyPrice.date == date)
            .all()
        }
        tickers_to_fetch = [t for t in all_ingest if t not in already_fetched]

        if not tickers_to_fetch:
            logger.info("All tickers already ingested for date — skipping fetch", date=date)
            ingested = 0
        else:
            logger.info(
                "Fetching missing tickers",
                date=date,
                missing=len(tickers_to_fetch),
                already_have=len(already_fetched),
            )
            results = ingest_tickers(tickers_to_fetch, start=fetch_start, end=date, db=db)
            ingested = sum(1 for v in results.values() if v > 0)
            logger.info("OHLCV ingested", date=date, tickers=ingested, total=len(tickers_to_fetch))

        # Step 3: Compute and persist active universe for *date* (skip if already exists)
        universe_exists = (
            db.query(DailyActiveUniverse).filter(DailyActiveUniverse.date == date).first()
            is not None
        )
        if universe_exists:
            logger.info("Universe already computed for date — skipping", date=date)
            from sqlalchemy import func
            universe_size = (
                db.query(func.count(DailyActiveUniverse.id))
                .filter(DailyActiveUniverse.date == date)
                .scalar() or 0
            )
            universe_df = None
        else:
            universe_df = compute_active_universe(date, db)
            universe_size = len(universe_df) if not universe_df.empty else 0
            if not universe_df.empty:
                persist_universe(date, universe_df, db)

        # Step 4: Integrity checks for *date* specifically
        issues = validate_data_integrity(db, date)

    return {
        "date": date.isoformat(),
        "fetch_start": fetch_start.isoformat(),
        "tickers_ingested": ingested,
        "universe_size": universe_size,
        "integrity_issues": issues,
    }