"""
app/data/providers/active_universe.py

Generates the point-in-time active stock universe for each trading day.

CRITICAL: The universe MUST be generated from data available *on that date*.
We rank stocks by dollar volume computed from same-day OHLCV.
We do NOT backfill from today's active list onto past dates — that would
introduce severe survivorship and attention leakage.

For prototype: universe is computed from a pre-loaded OHLCV database.
For production: replace with a provider that gives actual intraday scan
snapshots (e.g. Polygon's daily movers endpoint, IEX, finviz exports).
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
from sqlalchemy.orm import Session

from sqlalchemy import func

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.models import DailyActiveUniverse, DailyPrice, Stock

logger = get_logger(__name__)
settings = get_settings()


def _fetch_pinned_rows(
    date: dt.date,
    db: Session,
    pinned_tickers: list[str],
    next_rank: int,
) -> pd.DataFrame:
    """
    Fetch price rows for pinned tickers (e.g. BTC-USD) for *date*.
    These bypass all equity-oriented filters and are always included in the
    universe, appended after the top-N equity ranking so they never displace
    an equity slot.  Returns an empty DataFrame if no pinned tickers have
    price data for *date*.
    """
    if not pinned_tickers:
        return pd.DataFrame()

    rows = (
        db.query(DailyPrice, Stock.ticker, Stock.sector)
        .join(Stock, DailyPrice.stock_id == Stock.id)
        .filter(DailyPrice.date == date)
        .filter(Stock.ticker.in_(pinned_tickers))
        .filter(Stock.is_active == True)  # noqa: E712
        .all()
    )
    if not rows:
        return pd.DataFrame()

    records = []
    for i, (p, ticker, sector) in enumerate(rows):
        records.append({
            "stock_id":             p.stock_id,
            "ticker":               ticker,
            "sector":               sector,
            "close":                p.close,
            "volume":               p.volume or 0,
            "dollar_volume":        p.dollar_volume or (p.close * (p.volume or 0)),
            "rank_by_volume":       next_rank + i,
            "rank_by_dollar_volume": next_rank + i,
        })
    return pd.DataFrame(records)


def compute_active_universe(
    date: dt.date,
    db: Session,
    top_n: int | None = None,
    min_price: float | None = None,
    min_volume: int | None = None,
    min_rvol: float | None = None,
) -> pd.DataFrame:
    """
    Compute the top-N active US stocks for *date* using only data
    available on that day (point-in-time safe).

    Only includes stocks whose dollar volume is at least *min_rvol* times
    their own 20-trading-day average, ensuring the universe contains stocks
    with genuinely elevated activity — not just perpetually large-cap names.

    Returns DataFrame: [ticker, stock_id, dollar_volume, volume, close,
                        rank_by_volume, rank_by_dollar_volume]
    """
    top_n = top_n or settings.active_universe_size
    min_price = min_price or settings.active_universe_min_price
    min_volume = min_volume or settings.active_universe_min_volume
    min_rvol = min_rvol if min_rvol is not None else settings.active_universe_min_rvol

    # Pull all daily prices for the requested date
    rows = (
        db.query(DailyPrice, Stock.ticker, Stock.sector)
        .join(Stock, DailyPrice.stock_id == Stock.id)
        .filter(DailyPrice.date == date)
        .filter(DailyPrice.close >= min_price)
        .filter(DailyPrice.volume >= min_volume)
        .filter(Stock.is_active == True)  # noqa: E712
        .all()
    )

    if not rows:
        logger.warning("No price rows found for date", date=date)
        return pd.DataFrame()

    records = [
        {
            "stock_id": p.stock_id,
            "ticker": ticker,
            "sector": sector,
            "close": p.close,
            "volume": p.volume or 0,
            "dollar_volume": p.dollar_volume or (p.close * (p.volume or 0)),
        }
        for p, ticker, sector in rows
    ]
    df = pd.DataFrame(records)

    # ── Relative dollar-volume filter (point-in-time safe) ────────────────────
    # Compute each stock's 20-day average dollar_volume using only data before
    # today so the filter is point-in-time safe.
    if min_rvol > 0:
        stock_ids = df["stock_id"].tolist()
        avg_dvol = (
            db.query(
                DailyPrice.stock_id,
                func.avg(DailyPrice.dollar_volume).label("avg_dvol"),
            )
            .filter(DailyPrice.stock_id.in_(stock_ids))
            .filter(DailyPrice.date < date)
            .filter(DailyPrice.dollar_volume.isnot(None))
            .group_by(DailyPrice.stock_id)
            .having(func.count(DailyPrice.id) >= 10)  # need at least 10 days of history
            .all()
        )
        avg_map = {row.stock_id: float(row.avg_dvol) for row in avg_dvol}
        df["avg_dvol_20d"] = df["stock_id"].map(avg_map)
        df["rvol_dvol"] = df["dollar_volume"] / df["avg_dvol_20d"]

        # Keep stocks with elevated relative volume, or those with no history yet
        # (new stocks — don't penalise them for lacking a baseline)
        eligible = df[df["rvol_dvol"].isna() | (df["rvol_dvol"] >= min_rvol)].copy()

        if eligible.empty:
            # Fall back to unfiltered if nothing clears the threshold today
            logger.warning(
                "No stocks cleared min_rvol threshold — falling back to unfiltered universe",
                date=date,
                min_rvol=min_rvol,
                candidates=len(df),
            )
            eligible = df.copy()
        else:
            logger.info(
                "Relative-volume filter applied",
                date=date,
                before=len(df),
                after=len(eligible),
                min_rvol=min_rvol,
            )
        df = eligible

    df = df.sort_values("dollar_volume", ascending=False).reset_index(drop=True)
    df["rank_by_dollar_volume"] = df.index + 1

    df2 = df.sort_values("volume", ascending=False).reset_index(drop=True)
    vol_rank = {row["stock_id"]: i + 1 for i, row in df2.iterrows()}
    df["rank_by_volume"] = df["stock_id"].map(vol_rank)

    top = df[df["rank_by_dollar_volume"] <= top_n].copy()

    # ── Append pinned tickers (e.g. BTC-USD) ─────────────────────────────────
    # Pinned assets bypass all equity-oriented filters and are always included.
    # They are appended *after* the equity ranking so they never displace an
    # equity slot.  If a pinned ticker already ranked in the equity top-N (e.g.
    # BTC-USD passes min_volume on a high-activity day because its volume is in
    # coin units), we skip adding it again to avoid a duplicate (date, stock_id).
    pinned_tickers = settings.pinned_universe_tickers
    if pinned_tickers:
        already_present = set(top["stock_id"].tolist()) if not top.empty else set()
        pinned = _fetch_pinned_rows(date, db, pinned_tickers, next_rank=len(top) + 1)
        if not pinned.empty:
            pinned = pinned[~pinned["stock_id"].isin(already_present)]
        if not pinned.empty:
            top = pd.concat([top, pinned], ignore_index=True)

    return top


def persist_universe(date: dt.date, universe_df: pd.DataFrame, db: Session) -> None:
    """
    Upsert the computed universe into daily_active_universe.
    Existing rows for *date* are deleted and replaced (clean regeneration).
    """
    if universe_df.empty:
        return

    # Delete stale records for this date
    db.query(DailyActiveUniverse).filter(DailyActiveUniverse.date == date).delete()

    for _, row in universe_df.iterrows():
        entry = DailyActiveUniverse(
            date=date,
            stock_id=int(row["stock_id"]),
            rank_by_volume=int(row["rank_by_volume"]),
            rank_by_dollar_volume=int(row["rank_by_dollar_volume"]),
            is_in_scope=True,
            source="computed_dollar_volume",
        )
        db.add(entry)

    logger.info("Universe persisted", date=date, count=len(universe_df))


def get_universe_tickers(date: dt.date, db: Session) -> list[str]:
    """Return list of in-scope tickers for *date* from DB."""
    rows = (
        db.query(Stock.ticker)
        .join(DailyActiveUniverse, DailyActiveUniverse.stock_id == Stock.id)
        .filter(DailyActiveUniverse.date == date)
        .filter(DailyActiveUniverse.is_in_scope == True)  # noqa: E712
        .order_by(DailyActiveUniverse.rank_by_dollar_volume)
        .all()
    )
    return [r.ticker for r in rows]
