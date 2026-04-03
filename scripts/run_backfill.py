"""
scripts/run_backfill.py

Entry point for the historical data backfill.

Usage:
    python scripts/run_backfill.py
    python scripts/run_backfill.py --start 2020-01-01 --end 2024-12-31
    python scripts/run_backfill.py --skip-universe
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.jobs.backfill import app

if __name__ == "__main__":
    app()
