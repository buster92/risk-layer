"""
app/jobs/daily_predict.py

Daily prediction job. Runs after ingest (17:30 ET).
Steps:
  1. Get active universe tickers for today
  2. Compute and persist features for each ticker
  3. Run model predictions and classification
  4. Save DailyPrediction rows
"""
from __future__ import annotations

import datetime as dt

from app.core.logging import get_logger
from app.core.market_calendar import last_closed_trading_day
from app.data.providers.active_universe import get_universe_tickers
from app.features.feature_pipeline import persist_features_for_date
from app.services.stock_analysis_service import analyze_stock
from app.db.session import get_db

logger = get_logger(__name__)


def _predictions_already_exist(date: dt.date, tickers: list[str], db) -> bool:
    """Return True if all universe tickers already have predictions for *date*."""
    from app.db.models import DailyPrediction, Stock
    from app.core.config import get_settings
    settings = get_settings()
    existing = (
        db.query(DailyPrediction)
        .join(Stock, DailyPrediction.stock_id == Stock.id)
        .filter(
            DailyPrediction.date == date,
            DailyPrediction.model_version == settings.model_version,
            Stock.ticker.in_(tickers),
        )
        .count()
    )
    return existing >= len(tickers)


def run_daily_predict(date: dt.date | None = None) -> dict:
    date = date or last_closed_trading_day()
    logger.info("Daily predict starting", date=date)

    with get_db() as db:
        tickers = get_universe_tickers(date, db)
        if not tickers:
            logger.warning("No universe tickers found", date=date)
            return {"date": date.isoformat(), "processed": 0, "errors": 0}

        # Skip the entire predict step if all predictions already exist for this date.
        # This avoids redundant yfinance fetches (SPY + 12 sector ETFs) on re-runs,
        # which is the main source of slow/failing behavior in week validation reruns.
        if _predictions_already_exist(date, tickers, db):
            existing_count = len(tickers)
            logger.info(
                "Predictions already exist for date — skipping",
                date=date,
                count=existing_count,
            )
            return {"date": date.isoformat(), "processed": existing_count, "errors": 0}

        # Step 1: Compute features
        feature_count = persist_features_for_date(date, db, tickers=tickers)
        logger.info("Features computed", date=date, count=feature_count)

        # Step 2: Predictions + classifications
        success, errors = 0, 0
        for ticker in tickers:
            try:
                result = analyze_stock(ticker, date, db, persist=True)
                if result:
                    success += 1
                else:
                    errors += 1
            except Exception as exc:
                logger.error("Predict failed", ticker=ticker, error=str(exc))
                errors += 1

    logger.info("Daily predict complete", date=date, success=success, errors=errors)
    return {"date": date.isoformat(), "processed": success, "errors": errors}
