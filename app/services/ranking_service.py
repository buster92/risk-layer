"""
app/services/ranking_service.py

Produces ranked boards for:
  - Top deceptive moves today (highest deception_score)
  - Cleanest setups today (lowest risk_score)
  - Full active board sorted by user preference
"""
from __future__ import annotations

import datetime as dt
from typing import Literal

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.models import DailyActiveUniverse, DailyPrediction, Stock

logger = get_logger(__name__)
settings = get_settings()


def _base_board_query(date: dt.date, db: Session):
    """Base query joining predictions with stocks for a given date."""
    return (
        db.query(DailyPrediction, Stock.ticker, Stock.name, Stock.sector)
        .join(Stock, DailyPrediction.stock_id == Stock.id)
        .join(
            DailyActiveUniverse,
            (DailyActiveUniverse.stock_id == DailyPrediction.stock_id)
            & (DailyActiveUniverse.date == date),
        )
        .filter(DailyPrediction.date == date)
        .filter(DailyPrediction.model_version == settings.model_version)
        .filter(DailyActiveUniverse.is_in_scope == True)  # noqa: E712
    )


def _pred_to_dict(pred: DailyPrediction, ticker: str, name: str, sector: str, rank: int) -> dict:
    return {
        "rank": rank,
        "ticker": ticker,
        "company_name": name or ticker,
        "sector": sector,
        "classification": pred.classification,
        "interpretation": pred.interpretation,
        "confidence_bucket": pred.confidence_bucket,
        "p_continue_3d": pred.p_continue_3d,
        "p_continue_5d": pred.p_continue_5d,
        "p_drawdown_5d": pred.p_drawdown_5d,
        "p_mean_revert_3d": pred.p_mean_revert_3d,
        "risk_score": pred.risk_score,
        "deception_score": pred.deception_score,
        "setup_quality_score": pred.setup_quality_score,
        "flags": pred.explanation_flags or [],
    }


def get_active_board(
    date: dt.date,
    db: Session,
    sort_by: Literal["deceptive", "lowest_risk", "active"] = "active",
    limit: int = 50,
) -> list[dict]:
    """
    Return the full active-stock board for *date*.
    sort_by options:
      - "deceptive"    → most deceptive moves first
      - "lowest_risk"  → cleanest setups first (ascending risk_score)
      - "active"       → by active rank (dollar volume)
    """
    rows = _base_board_query(date, db).all()
    if not rows:
        return []

    items = [
        _pred_to_dict(pred, ticker, name, sector, 0)
        for pred, ticker, name, sector in rows
    ]

    if sort_by == "deceptive":
        items.sort(key=lambda x: x["deception_score"] or 0, reverse=True)
    elif sort_by == "lowest_risk":
        items.sort(key=lambda x: x["risk_score"] or 1.0, reverse=False)
    # else: DB order (by active rank from join)

    for i, item in enumerate(items):
        item["rank"] = i + 1

    return items[:limit]


def get_top_risks(date: dt.date, db: Session, limit: int = 10) -> list[dict]:
    """Top names ranked by deception risk."""
    return get_active_board(date, db, sort_by="deceptive", limit=limit)


def get_top_favorable(date: dt.date, db: Session, limit: int = 10) -> list[dict]:
    """Top names ranked by lowest risk (cleanest setups)."""
    return get_active_board(date, db, sort_by="lowest_risk", limit=limit)
