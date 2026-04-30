"""
app/models/train/base_trainer.py

Generic training logic shared by all four target models.
Each target gets its own train_*.py that calls this with its config.
"""
from __future__ import annotations

import json
import pickle
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
import datetime as dt

from app.core.config import get_settings
from app.core.constants import ALL_FEATURES, CONTINUATION_FEATURES
from app.core.logging import get_logger

try:
    import lightgbm as lgb
    LGB_AVAILABLE = True
except ImportError:
    LGB_AVAILABLE = False

logger = get_logger(__name__)
settings = get_settings()


def make_estimator():
    if LGB_AVAILABLE:
        import lightgbm as lgb
        return lgb.LGBMClassifier(
            n_estimators=300,
            learning_rate=0.05,
            max_depth=5,
            num_leaves=31,
            min_child_samples=30,
            subsample=0.8,
            colsample_bytree=0.8,
            reg_alpha=0.1,
            reg_lambda=0.1,
            random_state=42,
            verbose=-1,
        )
    else:
        from sklearn.ensemble import HistGradientBoostingClassifier
        return HistGradientBoostingClassifier(
            max_iter=300,
            learning_rate=0.05,
            max_depth=5,
            min_samples_leaf=30,
            random_state=42,
        )


def walk_forward_folds(
    dataset: pd.DataFrame,
    target: str,
    train_years: int,
    test_months: int,
    feature_cols: list[str] | None = None,
) -> list[dict]:
    feature_cols = feature_cols or ALL_FEATURES
    dataset = dataset.copy()
    dataset["date"] = pd.to_datetime(dataset["date"])
    dataset = dataset.sort_values("date")

    results = []
    min_date = dataset["date"].min()
    max_date = dataset["date"].max()
    fold_start = min_date + dt.timedelta(days=train_years * 365)
    test_delta = dt.timedelta(days=test_months * 30)
    fold = 0

    while fold_start + test_delta <= max_date:
        fold_end = fold_start + test_delta
        # Embargo: exclude rows within `wf_embargo_days` of the test fold boundary.
        # This prevents leaked labels from bleeding into the training set — a row
        # labeled at t=fold_start-1 uses forward prices through fold_start+4, so
        # naively including it in train would constitute look-ahead leakage.
        embargo_cutoff = fold_start - dt.timedelta(days=settings.wf_embargo_days)
        train = dataset[dataset["date"] < embargo_cutoff].dropna(subset=[target] + feature_cols)
        test = dataset[(dataset["date"] >= fold_start) & (dataset["date"] < fold_end)].dropna(
            subset=[target] + feature_cols
        )

        if len(train) < 200 or len(test) < 20 or len(np.unique(train[target].values)) < 2:
            fold_start = fold_end
            continue

        X_tr, y_tr = train[feature_cols].values, train[target].values.astype(int)
        X_te, y_te = test[feature_cols].values, test[target].values.astype(int)

        model = CalibratedClassifierCV(make_estimator(), method=settings.calibration_method, cv=5)
        model.fit(X_tr, y_tr)
        probs = model.predict_proba(X_te)[:, 1]
        base_rate = y_tr.mean()
        brier = brier_score_loss(y_te, probs)
        brier_base = brier_score_loss(y_te, np.full_like(probs, base_rate))

        results.append({
            "fold": fold,
            "target": target,
            "test_start": fold_start.date().isoformat(),
            "test_end": fold_end.date().isoformat(),
            "train_n": len(train),
            "test_n": len(test),
            "baseline_rate": round(float(base_rate), 4),
            "roc_auc": round(float(roc_auc_score(y_te, probs)), 4),
            "brier_score": round(float(brier), 4),
            "brier_skill": round(float(1 - brier / brier_base) if brier_base > 0 else 0, 4),
            "log_loss": round(float(log_loss(y_te, probs)), 4),
        })
        logger.info("WF fold", **results[-1])
        fold += 1
        fold_start = fold_end

    return results


def train_and_save(
    dataset: pd.DataFrame,
    target: str,
    skip_wf: bool = False,
    artifact_dir: str | None = None,
    feature_cols: list[str] | None = None,
) -> dict:
    feature_cols = feature_cols or ALL_FEATURES
    artifact_dir = artifact_dir or settings.model_artifacts_path
    wf_results = []
    if not skip_wf:
        wf_results = walk_forward_folds(
            dataset, target, settings.wf_train_years, settings.wf_test_months,
            feature_cols=feature_cols,
        )

    # Final model on all labeled data
    df = dataset.dropna(subset=[target] + feature_cols)
    X, y = df[feature_cols].values, df[target].values.astype(int)
    model = CalibratedClassifierCV(make_estimator(), method=settings.calibration_method, cv=5)
    model.fit(X, y)

    path = Path(artifact_dir)
    path.mkdir(parents=True, exist_ok=True)
    model_path = path / f"model_{target}_{settings.model_version}.pkl"
    metrics_path = path / f"model_{target}_{settings.model_version}_wf.json"

    with open(model_path, "wb") as f:
        pickle.dump(model, f)
    with open(metrics_path, "w") as f:
        json.dump(wf_results, f, indent=2)

    logger.info("Model saved", target=target, path=str(model_path), folds=len(wf_results))
    return {
        "target": target,
        "model_path": str(model_path),
        "wf_folds": len(wf_results),
        "avg_roc_auc": round(float(np.mean([r["roc_auc"] for r in wf_results])), 4) if wf_results else None,
        "avg_brier_skill": round(float(np.mean([r["brier_skill"] for r in wf_results])), 4) if wf_results else None,
    }
