"""app/api/routes/health.py"""
import datetime as dt

from fastapi import APIRouter

from app.core.config import get_settings
from app.models.inference.predictor import models_available

router = APIRouter()
settings = get_settings()


@router.get("/health")
def health():
    return {
        "status": "ok",
        "app": settings.app_name,
        "version": settings.app_version,
        "environment": settings.environment,
        "utc": dt.datetime.utcnow().isoformat(),
        "models_available": models_available(),
    }
