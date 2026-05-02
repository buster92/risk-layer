"""
app/models/train/train_continue_3d.py

Trains the continue_3d classifier (undirected — all qualifying days).

The direction-split experiment (continue_3d_up / continue_3d_dn) showed
that halving the training set hurt calibration more than specialisation helped.
The undirected model at AUC 0.708 / Brier skill 0.128 outperforms the split
models (0.604 / 0.689).  The directional routing infrastructure in predictor.py
is kept for the future — it activates automatically once directional artifacts
exist on disk and dataset size makes the split worthwhile.
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
