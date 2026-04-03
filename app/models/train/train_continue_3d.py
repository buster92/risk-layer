"""
app/models/train/train_continue_3d.py

Trains the continue_3d classifier using walk-forward cross-validation.
Uses LightGBM with sigmoid calibration and continuation-specific feature set.
"""
from __future__ import annotations

import pandas as pd

from app.core.constants import CONTINUATION_FEATURES, TARGET_CONTINUE_3D
from app.models.train.base_trainer import train_and_save


def run_training(dataset: pd.DataFrame, skip_wf: bool = False) -> dict:
    return train_and_save(
        dataset,
        TARGET_CONTINUE_3D,
        skip_wf=skip_wf,
        feature_cols=CONTINUATION_FEATURES,
    )
