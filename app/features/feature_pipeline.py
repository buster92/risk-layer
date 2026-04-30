"""
app/features/feature_pipeline.py

Orchestrates the full feature engineering pipeline for one or many stocks.
Produces a flat dataset where each row is (stock_id, date, features..., labels...).

Point-in-time safety is enforced here:
  - Features at date t use only data through t
  - Labels at date t use data from t+1 onward (set to NaN for last few rows)
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.constants import ALL_FEATURES, ALL_TARGETS
from app.core.logging import get_logger
from app.data.providers.benchmark_data import fetch_benchmark, fetch_sector_etfs
from app.db.models import DailyFeature, DailyPrice, Stock
from app.features.indicators import compute_all_indicators
from app.features.labeling import compute_labels
from app.features.regime_detection import compute_spy_regime
from app.features.relative_context import add_relative_context

logger = get_logger(__name__)
settings = get_settings()


def _load_stock_prices(stock_id: int, db: Session) -> pd.DataFrame:
    """Load all price rows for a stock from DB, sorted by date."""
    rows = (
        db.query(DailyPrice)
        .filter(DailyPrice.stock_id == stock_id)
        .order_by(DailyPrice.date)
        .all()
    )
    if not rows:
        return pd.DataFrame()

    return pd.DataFrame(
        [
            {
                "date": r.date,
                "open": r.open,
                "high": r.high,
                "low": r.low,
                "close": r.close,
                "adj_close": r.adj_close or r.close,
                "volume": r.volume or 0,
            }
            for r in rows
        ]
    )


def build_stock_features(
    stock: Stock,
    spy_df: pd.DataFrame,
    sector_df: pd.DataFrame,
    db: Session,
    compute_labels_flag: bool = True,
    spy_regime: "pd.Series | None" = None,
) -> pd.DataFrame:
    """
    Build the full feature + label frame for one stock.
    Returns DataFrame with all feature and label columns.
    Rows with insufficient history are dropped.

    Parameters
    ----------
    spy_regime : pre-computed HMM regime Series.  Pass None to compute inline
                 (only suitable for single-stock calls; multi-stock callers should
                 compute once via compute_spy_regime() and pass here).
    """
    price_df = _load_stock_prices(stock.id, db)
    if price_df.empty or len(price_df) < settings.min_history_days:
        logger.warning(
            "Insufficient history", ticker=stock.ticker, rows=len(price_df)
        )
        return pd.DataFrame()

    # Compute all indicators
    df = compute_all_indicators(price_df)

    # Add relative context (including beta_20d and market_regime_hmm)
    df = add_relative_context(df, spy_df, sector_df, stock.sector, spy_regime=spy_regime)

    # Add forward labels (NaN for recent rows where horizon not yet elapsed)
    if compute_labels_flag:
        df = compute_labels(df)

    # Add identifiers
    df["stock_id"] = stock.id
    df["ticker"] = stock.ticker
    df["feature_version"] = settings.feature_version

    # Drop rows without enough history (NaN features)
    df = df.dropna(subset=["ret_5d", "adx", "rel_vol_20d"])

    return df


def build_full_dataset(
    db: Session,
    start: dt.date | None = None,
    end: dt.date | None = None,
    tickers: list[str] | None = None,
) -> pd.DataFrame:
    """
    Build the complete training dataset across all (or specified) stocks.

    Returns a flat DataFrame: [stock_id, ticker, date, *features, *labels]
    """
    # Load benchmark and sector ETF data once for all stocks
    history_start = (start or dt.date(2018, 1, 1)) - dt.timedelta(days=365)
    history_end = end or dt.date.today()

    logger.info("Loading benchmark and sector data...")
    spy_df = fetch_benchmark(history_start, history_end)
    sector_df = fetch_sector_etfs(history_start, history_end)

    # Compute HMM regime once for all stocks (expensive — don't do it per-stock)
    logger.info("Computing SPY market regime...")
    try:
        spy_regime = compute_spy_regime(spy_df)
    except Exception as exc:
        logger.warning("Regime computation failed — using None fallback", error=str(exc))
        spy_regime = None

    # Load stocks
    query = db.query(Stock).filter(Stock.is_active == True)  # noqa: E712
    if tickers:
        query = query.filter(Stock.ticker.in_(tickers))
    stocks = query.all()

    logger.info("Building features", stock_count=len(stocks))
    frames = []

    for stock in stocks:
        try:
            df = build_stock_features(stock, spy_df, sector_df, db, spy_regime=spy_regime)
            if df.empty:
                continue

            if start:
                df = df[df["date"] >= start]
            if end:
                df = df[df["date"] <= end]

            frames.append(df)
        except Exception as exc:
            logger.error("Feature build failed", ticker=stock.ticker, error=str(exc))

    if not frames:
        return pd.DataFrame()

    dataset = pd.concat(frames, ignore_index=True)
    logger.info("Dataset built", rows=len(dataset), stocks=len(frames))
    return dataset


def persist_features_for_date(
    date: dt.date,
    db: Session,
    tickers: list[str] | None = None,
) -> int:
    """
    Compute and persist features to daily_features for *date* only.
    Used by the daily prediction job.
    Returns number of stocks processed.
    """
    query = db.query(Stock).filter(Stock.is_active == True)  # noqa: E712
    if tickers:
        query = query.filter(Stock.ticker.in_(tickers))
    stocks = query.all()

    # Determine which stocks actually need feature computation.
    # Skip yfinance fetches entirely if all features already exist in the DB.
    already_done = {
        stock_id for (stock_id,) in db.query(DailyFeature.stock_id)
        .filter(
            DailyFeature.date == date,
            DailyFeature.feature_version == settings.feature_version,
            DailyFeature.stock_id.in_([s.id for s in stocks]),
        )
        .all()
    }
    stocks_needing_features = [s for s in stocks if s.id not in already_done]

    if not stocks_needing_features:
        count = len(already_done)
        logger.info("All features already computed for date — skipping", date=date, count=count)
        return count

    history_start = date - dt.timedelta(days=365 + 30)
    spy_df = fetch_benchmark(history_start, date)
    sector_df = fetch_sector_etfs(history_start, date)

    try:
        spy_regime = compute_spy_regime(spy_df)
    except Exception as exc:
        logger.warning("Regime computation failed — using None fallback", error=str(exc))
        spy_regime = None

    count = len(already_done)  # start from already-done count
    for stock in stocks_needing_features:
        try:
            df = build_stock_features(
                stock, spy_df, sector_df, db,
                compute_labels_flag=False, spy_regime=spy_regime,
            )
            if df.empty:
                continue

            row = df[df["date"] == date]
            if row.empty:
                continue

            feature_dict = {
                col: (None if pd.isna(v) else v)
                for col, v in row[ALL_FEATURES].iloc[0].items()
            }

            # Upsert
            existing = (
                db.query(DailyFeature)
                .filter(
                    DailyFeature.stock_id == stock.id,
                    DailyFeature.date == date,
                    DailyFeature.feature_version == settings.feature_version,
                )
                .first()
            )
            if existing:
                existing.features = feature_dict
            else:
                db.add(
                    DailyFeature(
                        stock_id=stock.id,
                        date=date,
                        feature_version=settings.feature_version,
                        features=feature_dict,
                    )
                )
            count += 1
        except Exception as exc:
            logger.error("Feature persist failed", ticker=stock.ticker, error=str(exc))

    db.flush()
    logger.info("Features persisted for date", date=date, count=count)
    return count
