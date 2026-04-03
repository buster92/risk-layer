"""
app/api/routes/stocks.py

All public stock endpoints per spec Section 13.
"""
from __future__ import annotations

import datetime as dt
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.market_calendar import last_closed_trading_day
from app.db.session import get_db as _get_db
from app.services.digest_service import build_daily_digest
from app.services.ranking_service import (
    get_active_board,
    get_top_favorable,
    get_top_risks,
)
from app.services.stock_analysis_service import analyze_stock
from app.db.models import DailyPrediction, Stock

router = APIRouter()


def get_db():
    with _get_db() as db:
        yield db


def _parse_date(date_str: str | None) -> dt.date:
    if date_str:
        try:
            return dt.date.fromisoformat(date_str)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid date format: {date_str!r}")
    return last_closed_trading_day()


# ── GET /market/active ─────────────────────────────────────────────────────────
@router.get("/market/active")
def get_active_board_endpoint(
    date: str | None = Query(None, description="YYYY-MM-DD (default: last closed trading day)"),
    sort_by: Literal["deceptive", "lowest_risk", "active"] = Query("active"),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """
    Current daily active-stock board.
    Returns classification, probabilities, and flags for each covered stock.
    """
    target_date = _parse_date(date)
    board = get_active_board(target_date, db, sort_by=sort_by, limit=limit)
    return {
        "date": target_date.isoformat(),
        "sort_by": sort_by,
        "count": len(board),
        "stocks": board,
    }


# ── GET /stocks/{ticker}/analysis ─────────────────────────────────────────────
@router.get("/stocks/{ticker}/analysis")
def get_stock_analysis(
    ticker: str,
    date: str | None = Query(None),
    db: Session = Depends(get_db),
):
    """
    Full credibility analysis for a single ticker on a given date.
    """
    ticker = ticker.upper()
    target_date = _parse_date(date)

    # Try DB first — re-run if not found
    from app.core.config import get_settings
    settings = get_settings()

    existing = (
        db.query(DailyPrediction)
        .join(Stock, DailyPrediction.stock_id == Stock.id)
        .filter(Stock.ticker == ticker)
        .filter(DailyPrediction.date == target_date)
        .filter(DailyPrediction.model_version == settings.model_version)
        .first()
    )

    if existing:
        return {
            "ticker": ticker,
            "date": target_date.isoformat(),
            "classification": existing.classification,
            "interpretation": existing.interpretation,
            "confidence_bucket": existing.confidence_bucket,
            "probabilities": {
                "p_continue_3d": existing.p_continue_3d,
                "p_continue_5d": existing.p_continue_5d,
                "p_drawdown_5d": existing.p_drawdown_5d,
                "p_mean_revert_3d": existing.p_mean_revert_3d,
            },
            "flags": existing.explanation_flags or [],
            "risk_score": existing.risk_score,
            "deception_score": existing.deception_score,
            "setup_quality_score": existing.setup_quality_score,
        }

    result = analyze_stock(ticker, target_date, db, persist=True)
    if not result:
        raise HTTPException(
            status_code=404,
            detail=f"No analysis available for {ticker} on {target_date}. "
                   "Run daily ingest and predict jobs first.",
        )
    return result


# ── GET /market/top-risks ──────────────────────────────────────────────────────
@router.get("/market/top-risks")
def get_top_risks_endpoint(
    date: str | None = Query(None),
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
):
    """Ranked list of most deceptive moves today."""
    target_date = _parse_date(date)
    risks = get_top_risks(target_date, db, limit=limit)
    return {"date": target_date.isoformat(), "count": len(risks), "stocks": risks}


# ── GET /market/top-favorable ─────────────────────────────────────────────────
@router.get("/market/top-favorable")
def get_top_favorable_endpoint(
    date: str | None = Query(None),
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
):
    """Ranked list of cleanest setups (lowest risk) today."""
    target_date = _parse_date(date)
    favorable = get_top_favorable(target_date, db, limit=limit)
    return {"date": target_date.isoformat(), "count": len(favorable), "stocks": favorable}


# ── GET /stocks/{ticker}/history ───────────────────────────────────────────────
@router.get("/stocks/{ticker}/history")
def get_stock_history(
    ticker: str,
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
):
    """Recent classification history and realized outcomes for a ticker."""
    ticker = ticker.upper()
    stock = db.query(Stock).filter(Stock.ticker == ticker).first()
    if not stock:
        raise HTTPException(status_code=404, detail=f"Stock {ticker} not found")

    cutoff = dt.date.today() - dt.timedelta(days=days)
    preds = (
        db.query(DailyPrediction)
        .filter(DailyPrediction.stock_id == stock.id)
        .filter(DailyPrediction.date >= cutoff)
        .order_by(DailyPrediction.date.desc())
        .all()
    )

    return {
        "ticker": ticker,
        "history": [
            {
                "date": p.date.isoformat(),
                "classification": p.classification,
                "p_continue_3d": p.p_continue_3d,
                "p_drawdown_5d": p.p_drawdown_5d,
                "risk_score": p.risk_score,
                "flags": p.explanation_flags or [],
            }
            for p in preds
        ],
    }


# ── GET /digest/daily ──────────────────────────────────────────────────────────
@router.get("/digest/daily")
def get_daily_digest(
    date: str | None = Query(None),
    db: Session = Depends(get_db),
):
    """Daily digest payload — top risks, continuations, prior outcomes."""
    target_date = _parse_date(date)
    digest = build_daily_digest(target_date, db)
    return digest
