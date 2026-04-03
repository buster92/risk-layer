"""
scripts/run_train_all.py

Trains all four target models on the full historical dataset.
Run after backfill is complete.

Usage:
    python scripts/run_train_all.py
    python scripts/run_train_all.py --skip-wf   # skip walk-forward (faster, dev only)
    python scripts/run_train_all.py --tickers AAPL,MSFT,NVDA
"""
from __future__ import annotations

import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import typer
from app.core.logging import configure_logging, get_logger
from app.core.constants import ALL_TARGETS
from app.db.session import get_db

configure_logging()
logger = get_logger(__name__)

app = typer.Typer()


@app.command()
def main(
    skip_wf: bool = typer.Option(False, "--skip-wf", help="Skip walk-forward validation"),
    tickers: str = typer.Option(None, help="Comma-separated tickers to limit training data"),
    start: str = typer.Option(None, help="Training start date YYYY-MM-DD"),
    end: str = typer.Option(None, help="Training end date YYYY-MM-DD"),
):
    import datetime as dt
    from app.features.feature_pipeline import build_full_dataset

    ticker_list = [t.strip().upper() for t in tickers.split(",")] if tickers else None
    start_date = dt.date.fromisoformat(start) if start else None
    end_date = dt.date.fromisoformat(end) if end else None

    logger.info("Building training dataset...")
    with get_db() as db:
        dataset = build_full_dataset(db, start=start_date, end=end_date, tickers=ticker_list)

    if dataset.empty:
        logger.error("Dataset is empty — run backfill first")
        raise typer.Exit(1)

    logger.info("Dataset ready", rows=len(dataset), cols=len(dataset.columns))

    from app.models.train import train_continue_3d, train_continue_5d
    from app.models.train import train_drawdown_5d, train_mean_revert_3d

    trainers = [
        train_continue_3d,
        train_continue_5d,
        train_drawdown_5d,
        train_mean_revert_3d,
    ]

    results = []
    for trainer in trainers:
        logger.info("Training", module=trainer.__name__)
        try:
            summary = trainer.run_training(dataset, skip_wf=skip_wf)
            results.append(summary)
            logger.info("Done", **summary)
        except Exception as exc:
            logger.error("Training failed", module=trainer.__name__, error=str(exc))

    # Print summary table
    print("\n" + "=" * 70)
    print("TRAINING SUMMARY")
    print("=" * 70)
    for r in results:
        print(
            f"  {r['target']:<30}  "
            f"folds={r['wf_folds']}  "
            f"avg_auc={r.get('avg_roc_auc') or 'N/A'}  "
            f"avg_brier_skill={r.get('avg_brier_skill') or 'N/A'}"
        )
    print("=" * 70)
    print("\nAll models trained. Run `python scripts/run_daily.py` to generate predictions.")


if __name__ == "__main__":
    app()
