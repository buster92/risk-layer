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
Rule-based intraday trading decision layer. **RiskLayer (the model) decides WHAT to
trade; the execution engine decides WHEN and HOW to act.**

**Design principle.** All decisions are:
- deterministic — every threshold lives in `risklayer/execution/config.py`
- advisory only — the engine prints / returns recommendations; **no broker orders
  are placed and no money moves**
- LLM-free — no LLM is in the decision path; every output is the result of
  pure-Python rule evaluation
- structured — every call returns an `ExecutionDecision` with stable reason
  codes (`GAP_TOO_LARGE`, `RECLAIM_HELD`, `EDGE_COLLAPSE`, …) that downstream
  tooling can branch on

**Public API:**
- `evaluate_entry(candidate, candles_1m, candles_5m, atr, open_price, prev_close, latest_price)` →
  WAIT / ENTER / SKIP during the first **0–30 minutes** after the open.
  Returns suggested entry, stop, conservative TP (2R), and swing TP.
- `manage_position(position, latest_price, candles_15m, latest_metrics)` →
  HOLD / REDUCE / EXIT / TAKE_PROFIT_PARTIAL / TAKE_PROFIT_FULL. Called every 15 min.
- `evaluate_rotation(current_ticker, current_metrics, new_candidate_ticker,
  new_candidate_metrics, current_position_invalidated)` →
  HOLD / ROTATE. Compares the held position to a new RiskLayer candidate using
  the weighted RiskLayer score below.

**Entry rules** (entry_evaluator.py — top-to-bottom, first match wins):
1. Open gap too large: SKIP if `|open - prev_close| / ATR >= cfg.max_open_gap_atr`
   (**default 0.8**).
2. Price too extended from open: SKIP/WAIT if
   `|latest - open| / ATR >= cfg.max_current_extension_atr` (**default 0.8**).
   (SKIP when above open / chasing, WAIT when below open / flushed.)
3. Open-noise window: WAIT during the **first 5 minutes** after the open
   (`cfg.open_noise_minutes = 5`).
4. Pattern detection in the **5–30 minute** window
   (`cfg.entry_window_minutes = 30`):
   - invalid first → FAILED_BREAKOUT, UPPER_WICK_REJECTION, FIRST_BOUNCE_NO_BASE → WAIT
   - valid → FIVE_MIN_CONFIRMATION / PULLBACK_RECLAIM / RECLAIM_HELD → ENTER
5. ENTER returns mode `OPEN_ENTRY` (ATR stop, 2 ATR target) or `CONFIRMED_RECLAIM`
   (structure stop just below the reclaim level, 2R target + a swing TP).

**Position management** (position_manager.py — priority order):
- Stop hit → EXIT (CRITICAL)
- Take-profit hit → TAKE_PROFIT_FULL
- RiskLayer edge collapsed (`p_continue_5d <= 0.50` AND `p_drawdown_5d >= 0.40`) → EXIT
- 15m close below invalidation level → EXIT (`SWING_HOLD` ignores intraday-only breaks)
- 1.5R reached + momentum weakening → TAKE_PROFIT_PARTIAL (~40%)
- p_continue drop ≥ 0.08 OR p_drawdown rise ≥ 0.10 → REDUCE 30% (50% if both)
- 1R reached → annotate "move stop to break-even"
- otherwise → HOLD

**Portfolio rotation** (portfolio_allocator.py):
`score = 0.45*p_continue_5d + 0.30*(1 - p_drawdown_5d) + 0.20*(1 - risk_score) + 0.05*setup_quality_score`.
- Δ < `rotation_score_delta` (0.08) → HOLD
- Current invalidated → ROTATE 100% (full exit + rotate)
- Δ ≥ `strong_rotation_score_delta` (0.15) → strong ROTATE 50–70%
- Δ ≥ 0.08 → modest ROTATE 30–50% (or "minor" 20% when just barely above threshold)

**Daily script — three modes** (`scripts/run_entry_check.py`):
- `--mode entry [--ticker SYM]` — pre/at-open candidate analysis
- `--mode manage --paper-position-file path.json` — 15-min check on an open paper position
- `--mode portfolio --paper-position-file path.json` — held position vs today's top candidate
- `--json` — also write the decision to `data/execution_decisions/YYYY-MM-DD/{ticker}.json`
- Legacy single-day check and `--backtest` paths are preserved (omit `--mode`).

**Config-driven thresholds** (`risklayer/execution/config.py`):
gap size, current extension, reclaim hold candles, reduce / exit thresholds,
score weights, rotation deltas. Override via `ExecutionConfig(...)` instances
or `DEFAULT_CONFIG.with_overrides(...)`.