"""
app/models/inference/predictor.py

Loads all four calibrated classifiers and produces probability predictions
for a given feature row or batch.

Thread-safe: models are loaded once at module import and cached.
"""
from __future__ import annotations

import pickle
from pathlib import Path
from functools import lru_cache

import numpy as np
import pandas as pd

from app.core.config import get_settings
from app.core.constants import ALL_FEATURES, ALL_TARGETS, CONTINUATION_FEATURES
from app.core.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()


def _model_path(target: str) -> Path:
    return Path(settings.model_artifacts_path) / f"model_{target}_{settings.model_version}.pkl"


def _feature_cols(target: str) -> list[str]:
    """Return the feature list the model for *target* was trained on."""
    if target in ("continue_3d", "continue_5d"):
        return CONTINUATION_FEATURES
    return ALL_FEATURES


@lru_cache(maxsize=8)
def _load_model(target: str):
    path = _model_path(target)
    if not path.exists():
        raise FileNotFoundError(
            f"Model artifact not found: {path}\n"
            "Run `python scripts/run_train_all.py` first."
        )
    with open(path, "rb") as f:
        model = pickle.load(f)
    logger.info("Model loaded", target=target, path=str(path))
    return model


def predict_row(features: dict) -> dict[str, float]:
    """
    Score a single observation (dict of feature_name → value).
    Returns dict of {target: probability}.
    Missing features are filled with 0 (model was trained with imputed data).
    Each target uses the feature set it was trained on.
    """
    results = {}
    for target in ALL_TARGETS:
        try:
            model = _load_model(target)
            cols = _feature_cols(target)
            row = pd.DataFrame(
                [[features.get(f, 0.0) or 0.0 for f in cols]],
                columns=cols,
            )
            prob = float(model.predict_proba(row)[0, 1])
            results[target] = round(prob, 4)
        except FileNotFoundError:
            results[target] = None
        except Exception as exc:
            logger.error("Prediction error", target=target, error=str(exc))
            results[target] = None

    return results


def predict_batch(feature_df: pd.DataFrame) -> pd.DataFrame:
    """
    Score a batch of observations.
    Input: DataFrame with at least ALL_FEATURES columns.
    Output: Same DataFrame with prediction columns appended.
    Each target uses the feature set it was trained on.
    """
    for target in ALL_TARGETS:
        try:
            model = _load_model(target)
            cols = _feature_cols(target)
            X = feature_df[cols].fillna(0)
            probs = model.predict_proba(X)[:, 1]
            feature_df[f"p_{target}"] = np.round(probs, 4)
        except FileNotFoundError:
            feature_df[f"p_{target}"] = None
        except Exception as exc:
            logger.error("Batch prediction error", target=target, error=str(exc))
            feature_df[f"p_{target}"] = None

    # Rename to match output schema
    rename = {
        f"p_{t}": col
        for t, col in [
            ("continue_3d", "p_continue_3d"),
            ("continue_5d", "p_continue_5d"),
            ("drawdown_gt_3pct_5d", "p_drawdown_5d"),
            ("mean_revert_3d", "p_mean_revert_3d"),
        ]
    }
    feature_df = feature_df.rename(columns=rename)
    return feature_df


def models_available() -> dict[str, bool]:
    """Check which model artifacts exist on disk."""
    return {target: _model_path(target).exists() for target in ALL_TARGETS}
