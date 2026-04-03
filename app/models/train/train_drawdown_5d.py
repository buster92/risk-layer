"""
app/models/train/train_drawdown_5d.py
"""
import pandas as pd
from app.models.train.base_trainer import train_and_save
from app.core.constants import TARGET_DRAWDOWN_5D


def run_training(dataset: pd.DataFrame, skip_wf: bool = False) -> dict:
    return train_and_save(dataset, TARGET_DRAWDOWN_5D, skip_wf=skip_wf)
