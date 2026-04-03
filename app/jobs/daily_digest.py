"""
app/jobs/daily_digest.py
"""
from __future__ import annotations
import datetime as dt
from app.core.logging import get_logger
from app.core.market_calendar import last_closed_trading_day
from app.services.digest_service import build_daily_digest
from app.db.session import get_db

logger = get_logger(__name__)


def run_daily_digest(date: dt.date | None = None) -> dict:
    date = date or last_closed_trading_day()
    logger.info("Building daily digest", date=date)
    with get_db() as db:
        digest = build_daily_digest(date, db)
    logger.info("Digest built", date=date, risks=len(digest["top_deceptive_moves"]))
    return digest
