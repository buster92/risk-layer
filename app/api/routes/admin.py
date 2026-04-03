"""
app/api/routes/admin.py

Admin endpoints for triggering jobs manually and checking system state.
Should be protected by auth in production.
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.session import get_db as _get_db
from app.models.inference.predictor import models_available

router = APIRouter()


def get_db():
    with _get_db() as db:
        yield db


@router.post("/jobs/ingest")
def trigger_ingest(date: str | None = Query(None)):
    from app.jobs.daily_ingest import run_daily_ingest
    d = dt.date.fromisoformat(date) if date else None
    result = run_daily_ingest(d)
    return result


@router.post("/jobs/predict")
def trigger_predict(date: str | None = Query(None)):
    from app.jobs.daily_predict import run_daily_predict
    d = dt.date.fromisoformat(date) if date else None
    result = run_daily_predict(d)
    return result


@router.post("/jobs/eval")
def trigger_eval():
    from app.jobs.weekly_eval import run_weekly_eval
    result = run_weekly_eval()
    return result


@router.get("/models/status")
def model_status():
    return {"models": models_available()}


@router.get("/eval/lift")
def get_lift_metrics(lookback_days: int = Query(90), db: Session = Depends(get_db)):
    from app.jobs.weekly_eval import compute_lift_by_bucket
    return compute_lift_by_bucket(db, lookback_days=lookback_days)
