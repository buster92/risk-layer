"""
app/api/routes/alerts.py

Alert CRUD endpoints.
POST  /v1/alerts          — create alert
GET   /v1/alerts          — list user alerts
DELETE /v1/alerts/{id}    — deactivate alert

Auth is a stub for v1 — wire up JWT middleware before production.
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session

from app.db.models import Alert, User
from app.db.schemas import AlertCreate, AlertResponse
from app.db.session import get_db as _get_db

router = APIRouter()


def get_db():
    with _get_db() as db:
        yield db


def _get_user_id(x_user_id: str | None = Header(None)) -> int:
    """
    Stub: extract user_id from request header.
    Replace with proper JWT decode in production.
    """
    if x_user_id is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    try:
        return int(x_user_id)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID")


@router.post("/alerts", response_model=AlertResponse, status_code=201)
def create_alert(
    payload: AlertCreate,
    user_id: int = Depends(_get_user_id),
    db: Session = Depends(get_db),
):
    """Create a new alert rule for the authenticated user."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    alert = Alert(
        user_id=user_id,
        ticker=payload.ticker.upper(),
        condition=payload.condition,
        threshold=payload.threshold,
        active_flag=True,
    )
    db.add(alert)
    db.flush()
    return AlertResponse(
        id=alert.id,
        ticker=alert.ticker,
        condition=alert.condition,
        threshold=alert.threshold,
        active_flag=alert.active_flag,
        created_at=alert.created_at or dt.datetime.utcnow(),
    )


@router.get("/alerts", response_model=list[AlertResponse])
def list_alerts(
    user_id: int = Depends(_get_user_id),
    db: Session = Depends(get_db),
):
    """List all active alerts for the authenticated user."""
    alerts = (
        db.query(Alert)
        .filter(Alert.user_id == user_id, Alert.active_flag == True)  # noqa: E712
        .order_by(Alert.created_at.desc())
        .all()
    )
    return [
        AlertResponse(
            id=a.id,
            ticker=a.ticker,
            condition=a.condition,
            threshold=a.threshold,
            active_flag=a.active_flag,
            created_at=a.created_at or dt.datetime.utcnow(),
        )
        for a in alerts
    ]


@router.delete("/alerts/{alert_id}", status_code=204)
def delete_alert(
    alert_id: int,
    user_id: int = Depends(_get_user_id),
    db: Session = Depends(get_db),
):
    """Deactivate (soft-delete) an alert."""
    alert = (
        db.query(Alert)
        .filter(Alert.id == alert_id, Alert.user_id == user_id)
        .first()
    )
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    alert.active_flag = False
