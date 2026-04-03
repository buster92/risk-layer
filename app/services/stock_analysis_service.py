"""
app/services/stock_analysis_service.py

Orchestrates the full analysis pipeline for one stock on a given date:
  1. Load features from DB (or compute on-the-fly)
  2. Run model predictions
  3. Map to classification + interpretation + flags
  4. Persist DailyPrediction
  5. Return structured analysis dict

This is the core service consumed by the daily job and the API.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
from sqlalchemy.orm import Session

from app.classification.mapper import PredictionBundle, classify
from app.core.config import get_settings
from app.core.constants import ALL_FEATURES
from app.core.logging import get_logger
from app.db.models import DailyActiveUniverse, DailyFeature, DailyPrediction, Stock
from app.models.inference.predictor import predict_row

logger = get_logger(__name__)
settings = get_settings()


def _load_features(stock: Stock, date: dt.date, db: Session) -> dict | None:
    """Load feature dict from daily_features table."""
    row = (
        db.query(DailyFeature)
        .filter(
            DailyFeature.stock_id == stock.id,
            DailyFeature.date == date,
            DailyFeature.feature_version == settings.feature_version,
        )
        .first()
    )
    return row.features if row else None


def analyze_stock(
    ticker: str,
    date: dt.date,
    db: Session,
    persist: bool = True,
) -> dict | None:
    """
    Run full analysis for *ticker* on *date*.
    Returns dict with classification, probabilities, flags, interpretation.
    Returns None if stock or features not found.
    """
    stock = db.query(Stock).filter(Stock.ticker == ticker).first()
    if not stock:
        logger.warning("Stock not found", ticker=ticker)
        return None

    features = _load_features(stock, date, db)
    if not features:
        logger.warning("No features for stock", ticker=ticker, date=date)
        return None

    # Run model predictions
    probs = predict_row(features)

    # Build prediction bundle for classifier
    bundle = PredictionBundle(
        p_continue_3d=probs.get("continue_3d"),
        p_continue_5d=probs.get("continue_5d"),
        p_drawdown_5d=probs.get("drawdown_gt_3pct_5d"),
        p_mean_revert_3d=probs.get("mean_revert_3d"),
        rel_vol_20d=features.get("rel_vol_20d"),
        rvol_percentile=features.get("rvol_percentile"),
        adx=features.get("adx"),
        adx_slope=features.get("adx_slope"),
        di_spread=features.get("di_spread"),
        move_zscore=features.get("move_zscore"),
        dist_mean_atr=features.get("dist_mean_atr"),
        dist_sma20=features.get("dist_sma20"),
        range_expansion_ratio=features.get("range_expansion_ratio"),
        rvol_5d=features.get("rvol_5d"),
        ret_1d=features.get("ret_1d"),
        gap_pct=features.get("gap_pct"),
        sector_trend_state=features.get("sector_trend_state"),
        market_regime=features.get("market_regime"),
        exhaustion_flag=features.get("exhaustion_flag"),
        consec_days=features.get("consec_days"),
        hh_hl_flag=features.get("hh_hl_flag"),
        alpha_spy_1d=features.get("alpha_spy_1d"),
    )

    result = classify(bundle)

    analysis = {
        "ticker": ticker,
        "company_name": stock.name or ticker,
        "sector": stock.sector,
        "date": date.isoformat(),
        "classification": result.classification,
        "interpretation": result.interpretation,
        "confidence_bucket": result.confidence_bucket,
        "risk_score": result.risk_score,
        "deception_score": result.deception_score,
        "setup_quality_score": result.setup_quality_score,
        "flags": result.flags,
        "probabilities": {
            "p_continue_3d": probs.get("continue_3d"),
            "p_continue_5d": probs.get("continue_5d"),
            "p_drawdown_5d": probs.get("drawdown_gt_3pct_5d"),
            "p_mean_revert_3d": probs.get("mean_revert_3d"),
        },
        "top_signals": _extract_top_signals(features),
        "market_regime": features.get("market_regime"),
        "sector_trend_state": features.get("sector_trend_state"),
    }

    if persist:
        _persist_prediction(stock, date, analysis, probs, result, db)

    return analysis


def _extract_top_signals(features: dict, top_n: int = 5) -> list[dict]:
    """Return the most informative feature signals for the explanation UI (risk-first order)."""
    signals = [
        {"name": "Drawdown Risk", "value": None, "unit": ""},  # placeholder — filled from probs
        {"name": "Move Z-Score", "value": features.get("move_zscore"), "unit": "σ"},
        {"name": "Relative Volume", "value": features.get("rel_vol_20d"), "unit": "x avg"},
        {"name": "1D Return", "value": round((features.get("ret_1d") or 0) * 100, 2), "unit": "%"},
        {"name": "ADX", "value": features.get("adx"), "unit": ""},
        {"name": "ATR %", "value": round((features.get("atr_pct") or 0) * 100, 2), "unit": "%"},
        {"name": "Dist from 20 SMA", "value": round((features.get("dist_sma20") or 0) * 100, 2), "unit": "%"},
    ]
    return [s for s in signals if s["value"] is not None][:top_n]


def _persist_prediction(
    stock: Stock,
    date: dt.date,
    analysis: dict,
    probs: dict,
    result,
    db: Session,
) -> None:
    """Upsert the prediction into daily_predictions."""
    existing = (
        db.query(DailyPrediction)
        .filter(
            DailyPrediction.stock_id == stock.id,
            DailyPrediction.date == date,
            DailyPrediction.model_version == settings.model_version,
        )
        .first()
    )

    if existing:
        existing.p_continue_3d = probs.get("continue_3d")
        existing.p_continue_5d = probs.get("continue_5d")
        existing.p_drawdown_5d = probs.get("drawdown_gt_3pct_5d")
        existing.p_mean_revert_3d = probs.get("mean_revert_3d")
        existing.classification = result.classification
        existing.interpretation = result.interpretation
        existing.risk_score = result.risk_score
        existing.deception_score = result.deception_score
        existing.setup_quality_score = result.setup_quality_score
        existing.confidence_bucket = result.confidence_bucket
        existing.explanation_flags = result.flags
    else:
        db.add(
            DailyPrediction(
                stock_id=stock.id,
                date=date,
                p_continue_3d=probs.get("continue_3d"),
                p_continue_5d=probs.get("continue_5d"),
                p_drawdown_5d=probs.get("drawdown_gt_3pct_5d"),
                p_mean_revert_3d=probs.get("mean_revert_3d"),
                classification=result.classification,
                interpretation=result.interpretation,
                risk_score=result.risk_score,
                deception_score=result.deception_score,
                setup_quality_score=result.setup_quality_score,
                confidence_bucket=result.confidence_bucket,
                explanation_flags=result.flags,
                model_version=settings.model_version,
                feature_version=settings.feature_version,
            )
        )
