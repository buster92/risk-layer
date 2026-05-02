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

risklayer/execution/ — rule-based trading execution engine (intraday only)
  ├── entry_evaluator.py    — WAIT/ENTER/SKIP decisions (0–30min post-open)
  ├── position_manager.py    — HOLD/REDUCE/EXIT/TAKE_PROFIT decisions (15min updates)
  ├── portfolio_allocator.py — ROTATE/HOLD decisions (position vs candidate)
  ├── decision_types.py      — ExecutionDecision, ExecutionAction, TradeMode
  ├── config.py              — all thresholds (gap, stop placement, score deltas)
  └── _candles.py            — Candle utility + technical calculations

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

## Execution Engine (risklayer.execution)
Rule-based intraday trading decision layer. RiskLayer (the model) decides **WHAT** to
trade; execution engine decides **WHEN** and **HOW**.

**Design principle:** All decisions are deterministic, fully threshold-driven (no LLM),
and return structured `ExecutionDecision` objects with human-readable reasons.

**Public API:**
- `evaluate_entry(candidate, candles_1m, candles_5m, atr, open_price, prev_close)` →
  WAIT / ENTER / SKIP during first 0–30 minutes. Returns entry price, stop, targets.
- `manage_position(position, latest_price, candles_15m, latest_metrics)` →
  HOLD / REDUCE / EXIT / TAKE_PROFIT_PARTIAL / TAKE_PROFIT_FULL. Called every 15min.
- `evaluate_rotation(current_position, candidate, latest_price, invalidated)` →
  ROTATE / HOLD. Compares position vs new candidate using weighted RiskLayer score.

**Entry rules** (entry_evaluator.py — top-to-bottom):
1. Gap-to-ATR ratio: reject if ≥ cfg.max_open_gap_atr (default 2.5)
2. Price moved too far from open: reject if > cfg.max_move_from_open_pct (default 1.5%)
3. No reclaim of overnight structure: SKIP if body inside prev close range
4. Volume & candle patterns: require SMA20 > SMA50 on 5m, intraday momentum positive
5. Confidence math: blend RiskLayer probabilities with intraday pattern strength
Action: ENTER with suggested stop (ATR or structure-based), targets (2–4 ATR), trade mode.

**Position management** (position_manager.py):
- Stop loss placement: breach = EXIT (critical)
- Take profit tiers: partial at +1.5R, full at +3R (configurable)
- Invalidation: if new metrics degrade model edge, raise REDUCE/EXIT confidence
- R multiple tracking: know exact risk exposure at any price

**Portfolio rotation** (portfolio_allocator.py):
Scores: 0.45*p_continue_5d + 0.30*(1-p_drawdown_5d) + 0.20*(1-risk_score) + 0.05*setup_quality
Rotates if: new_score > current_score + cfg.rotation_score_delta AND (current invalidated OR strong_score_delta met)

**Config-driven thresholds** (config.py): gap size, move distance, stop ATR, target ATRs,
R-multiple cutoffs, score deltas. Override via ExecutionConfig instances.