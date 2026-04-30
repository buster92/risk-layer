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
from app.core.constants import Classification
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


# Continuation classifications the model considers genuine setups.
# Neutral / deceptive labels are excluded — they have no continuation thesis.
_CONTINUATION_CLASSES = {
    Classification.STRONG_CONTINUATION,
    Classification.FAVORABLE_SETUP,
    Classification.WEAK_CONTINUATION,
}

# Minimum p_continue_3d below which the model has no real signal.
# Anything under 0.45 is barely above the base rate — not worth acting on.
_MIN_P_CONTINUE_3D = 0.45


def get_top_continuation(
    date: dt.date,
    db: Session,
    limit: int = 10,
    min_p_continue: float = _MIN_P_CONTINUE_3D,
) -> list[dict]:
    """
    Return the strongest continuation candidates for *date*, ranked by
    setup quality, filtered to stocks where the model has a genuine
    continuation thesis.

    Filters applied (both must pass):
      1. classification IN (Strong continuation, Trend-confirming, Weak continuation)
      2. p_continue_3d >= min_p_continue  (default 0.45; pass 0.0 to disable)

    Sorted by setup_quality_score DESC, then p_continue_3d DESC.

    Returns an empty list when no stock meets the threshold — the caller
    should treat this as "NO SETUP TODAY", not fall back to a neutral name.

    Use this for: entry check script, digest continuation section, alerts.
    Do NOT use get_top_favorable() for these — it sorts by risk_score which
    selects "least deceptive" not "strongest continuation" (different things).
    """
    rows = _base_board_query(date, db).all()
    if not rows:
        return []

    items = [
        _pred_to_dict(pred, ticker, name, sector, 0)
        for pred, ticker, name, sector in rows
    ]

    # Filter to continuation-classified stocks with a real signal
    items = [
        it for it in items
        if it["classification"] in _CONTINUATION_CLASSES
        and (it["p_continue_3d"] or 0) >= min_p_continue
    ]

    if not items:
        return []

    # Best continuation setup first: setup_quality_score primary, p_continue_3d tiebreaker
    items.sort(
        key=lambda x: (x["setup_quality_score"] or 0, x["p_continue_3d"] or 0),
        reverse=True,
    )

    for i, item in enumerate(items):
        item["rank"] = i + 1

    return items[:limit]
