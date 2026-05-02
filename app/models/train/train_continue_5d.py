"""
app/models/train/train_continue_5d.py

Trains the continue_5d classifier (undirected — all qualifying days).
See train_continue_3d.py for the direction-split rationale and experiment results.
"""
from __future__ import annotations

import pandas as pd

from app.core.constants import CONTINUATION_FEATURES, TARGET_CONTINUE_5D
from app.models.train.base_trainer import train_and_save


def run_training(dataset: pd.DataFrame, skip_wf: bool = False) -> dict:
    return train_and_save(
        dataset,
        TARGET_CONTINUE_5D,
        skip_wf=skip_wf,
        feature_cols=CONTINUATION_FEATURES,
    )
