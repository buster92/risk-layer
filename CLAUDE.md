# MoveCred — Project Context

## What this is
Active Stock Move Credibility Engine. Classifies whether a high-attention
stock move is trustworthy, fragile, or a trap. Daily timeframe only, US equities.

## Stack
- Python 3.13, FastAPI, PostgreSQL, SQLAlchemy, LightGBM, yfinance (prototype)
- APScheduler for daily jobs, structlog for logging, pandas-market-calendars

## Project structure
app/core/        — config, logging, constants, market calendar
app/data/        — providers (yfinance/polygon), ingestion pipelines
app/features/    — indicators, labeling, feature pipeline
app/models/      — train/ and inference/ (4 targets: continue_3d/5d, drawdown, mean_revert)
app/classification/ — deterministic mapper: probs + flags → classification label
app/jobs/        — daily_ingest, daily_predict, daily_digest, weekly_eval, backfill
app/services/    — stock_analysis, ranking, digest, outcome tracking
app/api/routes/  — stocks, alerts, admin, health
app/tests/       — pytest, SQLite in-memory, no real DB needed

## Known quirks to remember
- yfinance fails on <7 day windows for recent dates — fixed in market_data.py (_MIN_FETCH_DAYS=7)
- Always call resolve_to_trading_day(date) before any DB query — weekends have no price rows
- structlog must use stdlib.LoggerFactory() not PrintLoggerFactory — PrintLogger has no .name
- Labels use trading day horizons (pandas-market-calendars), never calendar days
- Active universe is computed point-in-time from same-day OHLCV, never backfilled

## Run order from scratch
1. python scripts/run_backfill.py --start 2019-01-01
2. python scripts/run_train_all.py
3. python scripts/run_daily.py --date YYYY-MM-DD
4. uvicorn app.api.main:app --reload

## Models
4 separate LightGBM + isotonic calibration classifiers:
continue_3d, continue_5d, drawdown_gt_3pct_5d, mean_revert_3d
Artifacts saved to app/models/artifacts/
Walk-forward validation only — never random splits

## Classification labels (deterministic mapper in app/classification/mapper.py)
Strong continuation profile / Trend-confirming participation /
Breakout with exhaustion risk / Speculative spike /
Panic flush with unstable structure / High attention low trust /
Weak continuation setup / Neutral

## DB
PostgreSQL. Tables: stocks, daily_prices, daily_features, daily_predictions,
daily_active_universe, prediction_outcomes, users, alerts
create_all_tables() auto-runs on uvicorn startup — DB must exist first.