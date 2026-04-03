"""
app/services/outcome_service.py

Computes and persists realized outcomes for predictions after the horizon has elapsed.
Run weekly (or nightly) by the weekly_eval job.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.market_calendar import add_trading_days
from app.db.models import DailyPrediction, DailyPrice, PredictionOutcome, Stock

logger = get_logger(__name__)
settings = get_settings()


def realize_outcomes(as_of_date: dt.date, db: Session) -> int:
    """
    Find all predictions whose horizons have elapsed by *as_of_date*
    and compute their realized outcomes.
    Returns count of outcomes recorded.
    """
    # Horizon 5D is the longest — predictions older than 5 trading days are eligible
    cutoff = as_of_date  # we'll check per-prediction whether 5 days have passed

    pending = (
        db.query(DailyPrediction)
        .outerjoin(PredictionOutcome, PredictionOutcome.prediction_id == DailyPrediction.id)
        .filter(PredictionOutcome.id == None)  # no outcome yet  # noqa: E711
        .all()
    )

    count = 0
    for pred in pending:
        try:
            # Check if 5 trading days have elapsed
            horizon_end = add_trading_days(pred.date, settings.label_horizon_long)
            if horizon_end > as_of_date:
                continue  # horizon not yet elapsed

            outcome = _compute_outcome(pred, db)
            if outcome:
                db.add(outcome)
                count += 1
        except Exception as exc:
            logger.warning("Outcome computation failed", prediction_id=pred.id, error=str(exc))

    logger.info("Outcomes realized", count=count, as_of=as_of_date)
    return count


def _compute_outcome(pred: DailyPrediction, db: Session) -> PredictionOutcome | None:
    """
    Compute realized labels for a prediction by loading forward price data.
    """
    stock = db.query(Stock).filter(Stock.id == pred.stock_id).first()
    if not stock:
        return None

    # Load price data from pred.date through pred.date + 5 trading days
    forward_prices = (
        db.query(DailyPrice)
        .filter(DailyPrice.stock_id == pred.stock_id)
        .filter(DailyPrice.date > pred.date)
        .order_by(DailyPrice.date)
        .limit(settings.label_horizon_long + 2)
        .all()
    )

    if not forward_prices or len(forward_prices) < settings.label_horizon_short:
        return None

    base = (
        db.query(DailyPrice)
        .filter(DailyPrice.stock_id == pred.stock_id, DailyPrice.date == pred.date)
        .first()
    )
    if not base or not base.adj_close:
        return None

    c0 = base.adj_close
    thresh = settings.continuation_threshold_pct / 100.0
    draw_thresh = settings.drawdown_threshold_pct / 100.0

    closes = [p.adj_close for p in forward_prices if p.adj_close]
    lows = [p.low for p in forward_prices if p.low]
    highs = [p.high for p in forward_prices if p.high]

    # Infer direction from prediction date return
    ret_1d = (c0 / base.open - 1) if base.open else 0.0

    realized_cont_3d = None
    if len(closes) >= settings.label_horizon_short:
        c3 = closes[settings.label_horizon_short - 1]
        if ret_1d > 0:
            realized_cont_3d = bool(c3 >= c0 * (1 + thresh))
        elif ret_1d < 0:
            realized_cont_3d = bool(c3 <= c0 * (1 - thresh))

    realized_cont_5d = None
    if len(closes) >= settings.label_horizon_long:
        c5 = closes[settings.label_horizon_long - 1]
        if ret_1d > 0:
            realized_cont_5d = bool(c5 >= c0 * (1 + thresh))
        elif ret_1d < 0:
            realized_cont_5d = bool(c5 <= c0 * (1 - thresh))

    # Max adverse excursion in 5 days
    mae_5d = None
    if lows and ret_1d >= 0 and len(lows) >= settings.label_horizon_long:
        mae_5d = round((c0 - min(lows[:settings.label_horizon_long])) / c0, 4)
    elif highs and ret_1d < 0 and len(highs) >= settings.label_horizon_long:
        mae_5d = round((max(highs[:settings.label_horizon_long]) - c0) / c0, 4)

    realized_draw = bool(mae_5d >= draw_thresh) if mae_5d is not None else None

    return PredictionOutcome(
        prediction_id=pred.id,
        realized_continue_3d=realized_cont_3d,
        realized_continue_5d=realized_cont_5d,
        realized_drawdown_5d=realized_draw,
        realized_mean_revert_3d=None,  # computed separately if needed
        max_adverse_excursion_5d=mae_5d,
        realized_at=dt.datetime.utcnow(),
    )
