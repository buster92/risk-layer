"""
app/jobs/backfill.py

Historical backfill script.
Run once to populate:
  1. OHLCV for a predefined list of active US stocks over the backfill period
  2. Active universe membership per day (point-in-time computed from volume)
  3. Features + labels for the full training dataset

IMPORTANT: Point-in-time universe is computed day-by-day from historical OHLCV,
NOT backfilled from today's active list.
"""
from __future__ import annotations

import datetime as dt

import typer

from app.core.constants import BENCHMARK, SECTOR_ETFS
from app.core.logging import get_logger, configure_logging
from app.core.market_calendar import trading_days_between
from app.data.pipelines.ingest_prices import ingest_tickers
from app.data.providers.active_universe import compute_active_universe, persist_universe
from app.db.session import get_db, create_all_tables

logger = get_logger(__name__)
app = typer.Typer()

# Broad list of liquid US stocks to seed the backfill
# In production this should come from a point-in-time index membership source
SEED_TICKERS = [
    # Mega cap tech (always active)
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "GOOG", "META", "TSLA",
    "AMD", "INTC", "QCOM", "AVGO", "TXN", "MU", "AMAT", "LRCX",

    # Financials
    "JPM", "BAC", "GS", "MS", "WFC", "C", "BLK", "SCHW", "COF",

    # Healthcare / biotech (volatile, frequently active)
    "JNJ", "PFE", "MRNA", "BNTX", "ABBV", "LLY", "UNH", "CVS",

    # Energy
    "XOM", "CVX", "OXY", "COP", "SLB", "HAL",

    # Consumer
    "AMZN", "WMT", "COST", "TGT", "HD", "NKE", "SBUX",

    # High-beta / frequently most-active names
    "COIN", "RIVN", "LCID", "PLTR", "SOFI", "HOOD", "UPST",
    "ROKU", "SNAP", "PINS", "UBER", "LYFT", "ABNB", "DASH",
    "SQ", "PYPL", "SHOP", "SPOT", "RBLX",

    # Meme / retail favorites (critical for exhaustion pattern training)
    "GME", "AMC", "BB", "BBBY",  # BBBY may be delisted, yfinance will skip gracefully

    # ETFs for benchmark and sector
    "SPY", "QQQ", "IWM", "DIA",
] + SECTOR_ETFS


@app.command()
def run_backfill(
    start: str = typer.Option("2019-01-01", help="Backfill start date YYYY-MM-DD"),
    end: str = typer.Option(None, help="Backfill end date (default: today)"),
    skip_universe: bool = typer.Option(False, help="Skip universe computation"),
):
    configure_logging()
    start_date = dt.date.fromisoformat(start)
    end_date = dt.date.fromisoformat(end) if end else dt.date.today()

    logger.info("Backfill starting", start=start_date, end=end_date, tickers=len(SEED_TICKERS))

    create_all_tables()

    with get_db() as db:
        # Step 1: Ingest all OHLCV for seed tickers
        logger.info("Ingesting OHLCV...")
        results = ingest_tickers(SEED_TICKERS, start=start_date, end=end_date, db=db)
        logger.info("OHLCV ingest complete", ingested=sum(1 for v in results.values() if v > 0))

    if not skip_universe:
        # Step 2: Compute universe per trading day (point-in-time)
        logger.info("Computing daily active universe...")
        trading_days = trading_days_between(start_date, end_date)

        for ts in trading_days:
            day = ts.date()
            with get_db() as db:
                try:
                    universe_df = compute_active_universe(day, db)
                    if not universe_df.empty:
                        persist_universe(day, universe_df, db)
                except Exception as exc:
                    logger.warning("Universe computation failed", date=day, error=str(exc))

    logger.info("Backfill complete", start=start_date, end=end_date)


if __name__ == "__main__":
    app()
