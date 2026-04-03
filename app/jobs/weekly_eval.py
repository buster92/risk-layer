"""
app/jobs/weekly_eval.py

Runs weekly to:
  1. Realize prediction outcomes for elapsed horizons
  2. Compute lift metrics by classification bucket
  3. Log summary for trust / public track record page
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.models import DailyPrediction, PredictionOutcome
from app.db.session import get_db
from app.services.outcome_service import realize_outcomes

logger = get_logger(__name__)


def compute_lift_by_bucket(db: Session, lookback_days: int = 90) -> dict:
    """
    Compute hit rate and lift by classification for recent predictions.
    Compares each class against unconditional baseline rate.
    """
    cutoff = dt.date.today() - dt.timedelta(days=lookback_days)

    rows = (
        db.query(DailyPrediction, PredictionOutcome)
        .join(PredictionOutcome, PredictionOutcome.prediction_id == DailyPrediction.id)
        .filter(DailyPrediction.date >= cutoff)
        .filter(PredictionOutcome.realized_continue_3d != None)  # noqa: E711
        .all()
    )

    if not rows:
        return {"error": "No realized outcomes yet"}

    by_class: dict[str, list[int]] = defaultdict(list)
    all_outcomes = []

    for pred, outcome in rows:
        cls = pred.classification or "Unknown"
        realized = int(outcome.realized_continue_3d)
        by_class[cls].append(realized)
        all_outcomes.append(realized)

    baseline = sum(all_outcomes) / len(all_outcomes) if all_outcomes else 0.5
    result = {"baseline_continuation_rate": round(baseline, 4), "by_class": {}}

    for cls, outcomes in by_class.items():
        hit_rate = sum(outcomes) / len(outcomes)
        result["by_class"][cls] = {
            "count": len(outcomes),
            "hit_rate": round(hit_rate, 4),
            "lift_vs_baseline": round(hit_rate - baseline, 4),
            "favorable": "Favorable" in cls,
        }

    return result


def run_weekly_eval(as_of_date: dt.date | None = None) -> dict:
    as_of_date = as_of_date or dt.date.today()
    logger.info("Weekly eval starting", as_of=as_of_date)

    with get_db() as db:
        count = realize_outcomes(as_of_date, db)
        lift = compute_lift_by_bucket(db)

    logger.info("Weekly eval complete", outcomes_realized=count)
    return {"as_of": as_of_date.isoformat(), "outcomes_realized": count, "lift_metrics": lift}
