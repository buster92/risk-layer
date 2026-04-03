"""
scripts/run_daily.py

Manually trigger the full daily pipeline for a given date.
Useful for backfilling predictions or debugging a specific day.

Usage:
    python scripts/run_daily.py
    python scripts/run_daily.py --date 2024-11-15
    python scripts/run_daily.py --date 2024-11-15 --skip-ingest
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import datetime as dt
import typer
from app.core.logging import configure_logging, get_logger

configure_logging()
logger = get_logger(__name__)

app = typer.Typer()


@app.command()
def main(
    date: str = typer.Option(None, help="Date YYYY-MM-DD (default: last trading day)"),
    skip_ingest: bool = typer.Option(False, "--skip-ingest"),
    skip_predict: bool = typer.Option(False, "--skip-predict"),
    skip_digest: bool = typer.Option(False, "--skip-digest"),
):
    target_date = dt.date.fromisoformat(date) if date else None

    if not skip_ingest:
        from app.jobs.daily_ingest import run_daily_ingest
        logger.info("Step 1/3: Ingesting data...")
        result = run_daily_ingest(target_date)
        logger.info("Ingest done", **result)

    if not skip_predict:
        from app.jobs.daily_predict import run_daily_predict
        logger.info("Step 2/3: Generating predictions...")
        result = run_daily_predict(target_date)
        logger.info("Predict done", **result)

    if not skip_digest:
        from app.jobs.daily_digest import run_daily_digest
        logger.info("Step 3/3: Building digest...")
        result = run_daily_digest(target_date)
        logger.info("Digest done", date=result["date"], summary=result.get("summary"))

    print("\nDaily pipeline complete.")


if __name__ == "__main__":
    app()
