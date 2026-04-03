"""
app/db/schemas.py

Pydantic v2 response/request schemas for the FastAPI layer.
These are the shapes exposed to clients — separate from ORM models.
"""
from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, Field


# ── Probabilities ──────────────────────────────────────────────────────────────
class Probabilities(BaseModel):
    p_continue_3d: float | None = Field(None, ge=0, le=1, description="Probability of 3D continuation")
    p_continue_5d: float | None = Field(None, ge=0, le=1, description="Probability of 5D continuation")
    p_drawdown_5d: float | None = Field(None, ge=0, le=1, description="Probability of adverse excursion >3% in 5D")
    p_mean_revert_3d: float | None = Field(None, ge=0, le=1, description="Probability of mean reversion in 3D")


# ── Board item (compact) ───────────────────────────────────────────────────────
class BoardItem(BaseModel):
    rank: int
    ticker: str
    company_name: str
    sector: str | None
    classification: str
    interpretation: str | None
    confidence_bucket: str | None
    p_continue_3d: float | None
    p_continue_5d: float | None
    p_drawdown_5d: float | None
    p_mean_revert_3d: float | None
    risk_score: float | None
    deception_score: float | None
    setup_quality_score: float | None
    flags: list[str] = []


class ActiveBoardResponse(BaseModel):
    date: str
    sort_by: str
    count: int
    stocks: list[BoardItem]


# ── Signal ─────────────────────────────────────────────────────────────────────
class Signal(BaseModel):
    name: str
    value: float | None
    unit: str = ""


# ── Full ticker analysis ───────────────────────────────────────────────────────
class StockAnalysisResponse(BaseModel):
    ticker: str
    company_name: str | None
    sector: str | None
    date: str
    classification: str
    interpretation: str
    confidence_bucket: str | None
    risk_score: float | None
    deception_score: float | None
    setup_quality_score: float | None
    flags: list[str] = []
    probabilities: Probabilities
    top_signals: list[Signal] = []
    market_regime: int | None = None
    sector_trend_state: int | None = None


# ── History ────────────────────────────────────────────────────────────────────
class HistoryItem(BaseModel):
    date: str
    classification: str | None
    p_continue_3d: float | None
    p_drawdown_5d: float | None
    risk_score: float | None
    flags: list[str] = []


class StockHistoryResponse(BaseModel):
    ticker: str
    history: list[HistoryItem]


# ── Digest ─────────────────────────────────────────────────────────────────────
class PriorOutcome(BaseModel):
    ticker: str
    classification: str | None
    predicted_p_continue_3d: float | None
    realized_continue_3d: bool | None
    realized_drawdown_5d: bool | None
    max_adverse_excursion_5d: float | None


class DigestResponse(BaseModel):
    date: str
    top_deceptive_moves: list[BoardItem]
    strongest_continuation_profiles: list[BoardItem]
    prior_session_outcomes: list[PriorOutcome]
    summary: str


# ── Alerts ─────────────────────────────────────────────────────────────────────
class AlertCreate(BaseModel):
    ticker: str = Field(..., min_length=1, max_length=16)
    condition: Literal[
        "classification_equals",
        "p_continue_3d_below",
        "p_continue_3d_above",
        "p_drawdown_5d_above",
        "risk_score_above",
        "deception_score_above",
    ]
    threshold: float | None = None

    model_config = {"json_schema_extra": {
        "example": {
            "ticker": "TSLA",
            "condition": "deception_score_above",
            "threshold": 0.65,
        }
    }}


class AlertResponse(BaseModel):
    id: int
    ticker: str
    condition: str
    threshold: float | None
    active_flag: bool
    created_at: dt.datetime


# ── Health ─────────────────────────────────────────────────────────────────────
class HealthResponse(BaseModel):
    status: str
    app: str
    version: str
    environment: str
    utc: str
    models_available: dict[str, bool]


# ── Admin ──────────────────────────────────────────────────────────────────────
class JobResult(BaseModel):
    date: str | None = None
    tickers_ingested: int | None = None
    universe_size: int | None = None
    processed: int | None = None
    errors: int | None = None
    outcomes_realized: int | None = None
    integrity_issues: dict | None = None
