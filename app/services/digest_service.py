"""
app/services/digest_service.py

Generates the daily digest payload:
  - Top deceptive moves
  - Strongest continuation profiles
  - Yesterday's flagged names and realized outcomes (if available)

Free tier sees a limited version. Paid tier sees the full board.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.market_calendar import prev_trading_day
from app.db.models import DailyPrediction, PredictionOutcome, Stock
from app.services.ranking_service import get_top_continuation, get_top_risks

logger = get_logger(__name__)


def build_daily_digest(date: dt.date, db: Session) -> dict:
    """
    Build the full digest payload for *date*.
    Returns dict consumed by the API and email job.
    """
    top_risks = get_top_risks(date, db, limit=5)
    top_continuations = get_top_continuation(date, db, limit=5)

    # Yesterday's outcomes (if realized)
    prev_date = prev_trading_day(date)
    prior_outcomes = _get_prior_outcomes(prev_date, db)

    return {
        "date": date.isoformat(),
        "top_deceptive_moves": top_risks,
        "strongest_continuation_profiles": top_continuations,
        "prior_session_outcomes": prior_outcomes,
        "summary": _build_summary(top_risks, top_continuations, prior_outcomes),
    }


def _get_prior_outcomes(date: dt.date, db: Session) -> list[dict]:
    """Return realized outcome data for predictions made on *date*."""
    rows = (
        db.query(DailyPrediction, PredictionOutcome, Stock.ticker)
        .join(PredictionOutcome, PredictionOutcome.prediction_id == DailyPrediction.id)
        .join(Stock, DailyPrediction.stock_id == Stock.id)
        .filter(DailyPrediction.date == date)
        .all()
    )

    outcomes = []
    for pred, outcome, ticker in rows:
        outcomes.append({
            "ticker": ticker,
            "classification": pred.classification,
            "predicted_p_continue_3d": pred.p_continue_3d,
            "realized_continue_3d": outcome.realized_continue_3d,
            "realized_drawdown_5d": outcome.realized_drawdown_5d,
            "max_adverse_excursion_5d": outcome.max_adverse_excursion_5d,
        })

    return outcomes


def _build_summary(risks: list, continuations: list, outcomes: list) -> str:
    """Build a short human-readable summary string."""
    parts = []

    if risks:
        top_trap = risks[0]["ticker"]
        parts.append(f"Top deceptive move: {top_trap} ({risks[0]['classification']})")

    if continuations:
        top_cont = continuations[0]["ticker"]
        parts.append(f"Strongest continuation profile: {top_cont}")

    if outcomes:
        correct = sum(
            1 for o in outcomes
            if o.get("realized_continue_3d") is not None
            and o.get("predicted_p_continue_3d") is not None
        )
        parts.append(f"{len(outcomes)} prior predictions with realized outcomes available")

    return " | ".join(parts) if parts else "No digest data for this session."
