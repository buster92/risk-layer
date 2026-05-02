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
from app.core.constants import (
    ALL_FEATURES,
    ALL_TARGETS,
    CONTINUATION_DIRECTIONAL_TARGETS,
    CONTINUATION_FEATURES,
    TARGET_CONTINUE_3D,
    TARGET_CONTINUE_3D_DN,
    TARGET_CONTINUE_3D_UP,
    TARGET_CONTINUE_5D,
    TARGET_CONTINUE_5D_DN,
    TARGET_CONTINUE_5D_UP,
)
from app.core.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()


def _model_path(target: str) -> Path:
    return Path(settings.model_artifacts_path) / f"model_{target}_{settings.model_version}.pkl"


def _feature_cols(target: str) -> list[str]:
    """Return the feature list the model for *target* was trained on."""
    cont_targets = {
        TARGET_CONTINUE_3D, TARGET_CONTINUE_5D,
        TARGET_CONTINUE_3D_UP, TARGET_CONTINUE_3D_DN,
        TARGET_CONTINUE_5D_UP, TARGET_CONTINUE_5D_DN,
    }
    return CONTINUATION_FEATURES if target in cont_targets else ALL_FEATURES


@lru_cache(maxsize=16)
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


# Map each public continuation target to its (up_key, dn_key) directional pair.
_DIRECTIONAL_MAP: dict[str, tuple[str, str]] = {
    TARGET_CONTINUE_3D: (TARGET_CONTINUE_3D_UP, TARGET_CONTINUE_3D_DN),
    TARGET_CONTINUE_5D: (TARGET_CONTINUE_5D_UP, TARGET_CONTINUE_5D_DN),
}


def _resolve_continuation_target(target: str, ret_1d: float | None) -> str:
    """
    Return the actual model key to use for a continuation target.

    If directional models exist on disk, route based on the sign of ret_1d:
      ret_1d > 0  → up model   (up-day continuation)
      ret_1d < 0  → dn model   (down-day continuation)
      ret_1d == 0 or None → undirected model (fallback)

    Falls back to the undirected model whenever the directional artifact is missing,
    so the system degrades gracefully before the split models are trained.
    """
    if target not in _DIRECTIONAL_MAP or ret_1d is None:
        return target

    up_key, dn_key = _DIRECTIONAL_MAP[target]

    if ret_1d > 0 and _model_path(up_key).exists():
        return up_key
    if ret_1d < 0 and _model_path(dn_key).exists():
        return dn_key
    return target  # undirected fallback


def predict_row(features: dict) -> dict[str, float]:
    """
    Score a single observation (dict of feature_name → value).
    Returns dict of {target: probability} keyed by the PUBLIC target names.

    For continuation targets, routes to the direction-specific model when
    available (based on features["ret_1d"]), else uses the undirected model.
    Missing features are filled with 0.
    """
    ret_1d = features.get("ret_1d")
    results = {}

    for target in ALL_TARGETS:
        model_key = _resolve_continuation_target(target, ret_1d)
        try:
            model = _load_model(model_key)
            cols = _feature_cols(model_key)
            row = pd.DataFrame(
                [[features.get(f, 0.0) or 0.0 for f in cols]],
                columns=cols,
            )
            prob = float(model.predict_proba(row)[0, 1])
            results[target] = round(prob, 4)
        except FileNotFoundError:
            results[target] = None
        except Exception as exc:
            logger.error("Prediction error", target=target, model_key=model_key, error=str(exc))
            results[target] = None

    return results


def predict_batch(feature_df: pd.DataFrame) -> pd.DataFrame:
    """
    Score a batch of observations.
    Input: DataFrame with feature columns present.
    Output: Same DataFrame with prediction columns appended.

    For continuation targets, rows are split by the sign of ret_1d and scored
    by their direction-specific model, then recombined.  Falls back to the
    undirected model for any direction whose artifact is missing.
    """
    feature_df = feature_df.copy()

    for target in ALL_TARGETS:
        if target not in _DIRECTIONAL_MAP:
            # Risk / mean-revert models — score whole batch with one model
            try:
                model = _load_model(target)
                cols = _feature_cols(target)
                X = feature_df[cols].fillna(0)
                feature_df[f"p_{target}"] = np.round(model.predict_proba(X)[:, 1], 4)
            except FileNotFoundError:
                feature_df[f"p_{target}"] = None
            except Exception as exc:
                logger.error("Batch prediction error", target=target, error=str(exc))
                feature_df[f"p_{target}"] = None
            continue

        # Continuation targets — direction-aware scoring
        up_key, dn_key = _DIRECTIONAL_MAP[target]
        up_available = _model_path(up_key).exists()
        dn_available = _model_path(dn_key).exists()
        probs = np.full(len(feature_df), np.nan)

        ret_col = feature_df["ret_1d"] if "ret_1d" in feature_df.columns else None

        if ret_col is not None and (up_available or dn_available):
            up_mask = ret_col > 0
            dn_mask = ret_col < 0
            neutral_mask = ~(up_mask | dn_mask)

            for mask, model_key in [(up_mask, up_key), (dn_mask, dn_key)]:
                sub = feature_df[mask]
                if sub.empty:
                    continue
                avail = _model_path(model_key).exists()
                actual_key = model_key if avail else target
                try:
                    model = _load_model(actual_key)
                    cols = _feature_cols(actual_key)
                    X = sub[cols].fillna(0)
                    probs[mask.values] = model.predict_proba(X)[:, 1]
                except Exception as exc:
                    logger.error("Directional batch error", target=target, key=actual_key, error=str(exc))

            # Neutral rows and any remaining NaN → undirected fallback
            fallback_mask = neutral_mask | np.isnan(probs)
            if fallback_mask.any():
                try:
                    model = _load_model(target)
                    cols = _feature_cols(target)
                    X = feature_df[fallback_mask][cols].fillna(0)
                    probs[fallback_mask.values] = model.predict_proba(X)[:, 1]
                except FileNotFoundError:
                    pass  # leave as NaN — artifact simply doesn't exist yet
                except Exception as exc:
                    logger.error("Fallback batch error", target=target, error=str(exc))
        else:
            # No ret_1d or no directional models — score whole batch undirected
            try:
                model = _load_model(target)
                cols = _feature_cols(target)
                X = feature_df[cols].fillna(0)
                probs[:] = model.predict_proba(X)[:, 1]
            except FileNotFoundError:
                pass
            except Exception as exc:
                logger.error("Batch prediction error", target=target, error=str(exc))

        feature_df[f"p_{target}"] = np.where(np.isnan(probs), None, np.round(probs, 4))

    # Rename to match output schema
    rename = {
        f"p_{t}": col
        for t, col in [
            ("continue_3d",        "p_continue_3d"),
            ("continue_5d",        "p_continue_5d"),
            ("drawdown_gt_3pct_5d","p_drawdown_5d"),
            ("mean_revert_3d",     "p_mean_revert_3d"),
        ]
    }
    feature_df = feature_df.rename(columns=rename)
    return feature_df


def models_available() -> dict[str, bool]:
    """Check which model artifacts exist on disk (undirected + directional)."""
    status = {target: _model_path(target).exists() for target in ALL_TARGETS}
    for target in CONTINUATION_DIRECTIONAL_TARGETS:
        status[target] = _model_path(target).exists()
    return status
