"""
app/models/train/train_mean_revert_3d.py
"""
import pandas as pd
from app.models.train.base_trainer import train_and_save
from app.core.constants import TARGET_MEAN_REVERT_3D


def run_training(dataset: pd.DataFrame, skip_wf: bool = False) -> dict:
    return train_and_save(dataset, TARGET_MEAN_REVERT_3D, skip_wf=skip_wf)
