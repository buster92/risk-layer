# MoveCred — Active Stock Move Credibility Engine

> **"Is this active move trustworthy, fragile, or deceptive?"**

MoveCred is a decision layer for the most active US stocks that classifies whether a high-attention move is trustworthy, fragile, or likely to fail. It is not a generic indicator dashboard. It is a market-behavior classifier and continuation-risk engine.

---

## What it produces

For each covered stock, after daily market close:

| Output layer | Example |
|---|---|
| **Classification** | `Breakout with exhaustion risk` |
| **Probabilities** | `Continuation (3D): 31% vs 56% normal` |
| **Interpretation** | `This move is attracting heavy attention, but similar setups usually fail to build stable follow-through.` |
| **Flags** | `Relative volume elevated · Volatility expanding · Price extended from trend` |

---

## Architecture

```
movecred/
├── app/
│   ├── api/              FastAPI app + routes
│   │   └── routes/       health, stocks, admin
│   ├── core/             config, logging, market calendar, constants
│   ├── db/               SQLAlchemy models + session
│   ├── data/
│   │   ├── providers/    market_data, active_universe, benchmark, sector
│   │   └── pipelines/    ingest_prices, validate_data
│   ├── features/         indicators, relative_context, labeling, feature_pipeline
│   ├── models/
│   │   ├── train/        one trainer per target (continue_3d/5d, drawdown, mean_revert)
│   │   └── inference/    predictor (loads all four calibrated classifiers)
│   ├── classification/   deterministic mapper → class + flags + interpretation
│   ├── jobs/             daily_ingest, daily_predict, daily_digest, weekly_eval, backfill
│   ├── services/         stock_analysis, ranking, digest, outcome tracking
│   └── tests/
├── risklayer/
│   └── execution/        rule-based intraday execution engine
│       ├── entry_evaluator.py      WAIT / ENTER / SKIP decisions
│       ├── position_manager.py      HOLD / REDUCE / EXIT / TAKE_PROFIT decisions
│       ├── portfolio_allocator.py   ROTATE / HOLD decisions
│       ├── decision_types.py        ExecutionDecision, ExecutionAction, TradeMode
│       ├── config.py                threshold configuration
│       └── _candles.py              Candle utilities
├── scripts/              run_backfill, run_train_all, run_daily, run_entry_check, run_week_validation
└── docs/
```

---

## Quickstart

### 1. Install

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.example .env
# Edit DATABASE_URL and MARKET_DATA_PROVIDER
```
// This can also help initializing the database schema
uvicorn app.api.main:app --reload

### 3. Run PostgreSQL (Docker)

```bash
docker run -d \
  --name movecred-db \
  -e POSTGRES_USER=movecred \
  -e POSTGRES_PASSWORD=movecred \
  -e POSTGRES_DB=movecred \
  -p 5432:5432 \
  postgres:16
```

### 4. Backfill historical data

```bash
# Ingests OHLCV + builds point-in-time daily universes from 2019 onward
python3 scripts/run_backfill.py --start 2019-01-01
```

> **Warning:** This step may take 30–60 minutes depending on your data provider and internet connection.

### 5. Train models

```bash
# Trains all four classifiers with walk-forward validation
python3 scripts/run_train_all.py

# Skip walk-forward for a fast test run
python3 scripts/run_train_all.py --skip-wf
```

### 6. Run daily pipeline (manual)

```bash
python3 scripts/run_daily.py
# or for a specific date:
python3  scripts/run_daily.py --date 2024-11-15
```

### 7. Start the API

```bash
uvicorn app.api.main:app --reload
```

API is at `http://localhost:8000`. The scheduler runs jobs automatically after market close.

---

## Key API endpoints

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/health` | Service health + model availability |
| `GET` | `/v1/market/active` | Today's full active-stock board |
| `GET` | `/v1/market/top-risks` | Ranked deceptive moves |
| `GET` | `/v1/market/top-continuations` | Ranked continuation profiles |
| `GET` | `/v1/stocks/{ticker}/analysis` | Full ticker analysis |
| `GET` | `/v1/stocks/{ticker}/history` | Classification history |
| `GET` | `/v1/digest/daily` | Digest payload |
| `POST` | `/v1/admin/jobs/ingest` | Trigger ingest manually |
| `POST` | `/v1/admin/jobs/predict` | Trigger predictions manually |
| `GET` | `/v1/admin/eval/lift` | Lift metrics by classification bucket |

Interactive docs: `http://localhost:8000/docs`

---

## Targets and models

Four separate calibrated classifiers (LightGBM + isotonic calibration):

| Target | Description |
|---|---|
| `continue_3d` | Price continues in move direction over next 3 trading days |
| `continue_5d` | Same over next 5 trading days |
| `drawdown_gt_3pct_5d` | Adverse excursion > 3% in next 5 trading days |
| `mean_revert_3d` | Significant reversal within next 3 trading days |

Validation uses **walk-forward only** — no random splits. Default: 3-year training window, 3-month test windows, rolling.

---

## Classification rules (deterministic mapper)

Rules execute top-to-bottom; first match wins:

1. `Strong continuation profile` — p_continue_3d ≥ 0.65, ADX > 25 and strengthening, high relative volume, not overextended
2. `Trend-confirming participation` — p_continue_3d ≥ 0.58, ADX > 20, sector aligned
3. `Panic flush` — Sharp decline, high volume, elevated drawdown risk
4. `Breakout with exhaustion risk` — Exhaustion flag OR (extended + high vol + expanding vol + low continuation odds)
5. `Speculative spike` — High volume, weak sector, low continuation odds, large 1D move
6. `High attention, low trust` — High volume but low continuation odds or high drawdown risk
7. `Weak continuation setup` — Low continuation odds + high drawdown risk
8. `Neutral` — No dominant pattern

---

## Point-in-time safety

This is the most critical correctness requirement:

- **Universe**: computed per day from same-day OHLCV — never backfilled from today's active list
- **Features**: computed at day t using only data through day t
- **Labels**: computed using data from t+1 onward, NaN for final horizon rows
- **Market calendar**: all forward horizons count NYSE trading sessions, not calendar days

Violating any of these would produce deceptively good-looking backtests that fail in production.

---

## Free vs paid

| Feature | Free | Paid |
|---|---|---|
| Prior-day deceptive digest | ✓ | ✓ |
| Limited ticker checks/day | ✓ | ✓ |
| Same-day full board | — | ✓ |
| All probabilities | — | ✓ |
| Top risks ranked | — | ✓ |
| Top continuations ranked | — | ✓ |
| Alerts | — | ✓ |
| Historical pattern explorer | — | ✓ |

---

## Production migration checklist

- [ ] Swap `MARKET_DATA_PROVIDER=yfinance` → `polygon` and implement `PolygonProvider`
- [ ] Add proper JWT auth to admin routes
- [ ] Add free/paid gating middleware
- [ ] Set `SECRET_KEY` to a random 32-byte value
- [ ] Set `LOG_JSON=true` for structured cloud logging
- [ ] Deploy PostgreSQL to Render / Neon / RDS
- [ ] Set up cron or platform scheduler for daily jobs (or let APScheduler run in the app)
- [ ] Add alert delivery (email/webhook) in `alert_service.py`

---

## Utility scripts

### `run_entry_check.py` — Morning entry check + execution-engine front-end

The same script supports two families of modes:

**Legacy paths** (unchanged behaviour — call without `--mode`):

```bash
python3 scripts/run_entry_check.py                    # default: today's predictions
python3 scripts/run_entry_check.py --date 2024-11-15  # evaluate a specific past day
python3 scripts/run_entry_check.py --backtest --days 30 --fee 0.10
```

The legacy single-day path loads the top continuation candidate, downloads OHLCV + intraday via yfinance, computes 14-day ATR, and prints two simple gap-vs-ATR verdicts (`BUY / CAUTION / SKIP` at 0.5x / 1.0x cutoffs). When the open verdict is `BUY` it suggests a 1-ATR stop and 2-ATR target. If no stock passes `p_continue_3d ≥ 0.45` and a favorable classification it prints `NO SETUP TODAY` — do not substitute a neutral stock, wait for the next session.

**Execution-engine modes** (rule-based, structured `ExecutionDecision` output — see "Daily workflow" below).

```bash
# Pre/at-open candidate analysis (0–30 min entry timing)
python3 scripts/run_entry_check.py --mode entry
python3 scripts/run_entry_check.py --mode entry --ticker IWM

# 15-minute check on an open paper position
python3 scripts/run_entry_check.py --mode manage \
    --paper-position-file data/paper_positions/XLY.json

# Held position vs today's top RiskLayer candidate
python3 scripts/run_entry_check.py --mode portfolio \
    --paper-position-file data/paper_positions/XLY.json

# Add --json to also persist the decision to
# data/execution_decisions/YYYY-MM-DD/{ticker}.json
```

The execution engine is **advisory only** — it prints / writes recommendations and never places broker orders or moves money. There is **no LLM in the decision path**; every output is the result of pure-Python rule evaluation against the thresholds in `risklayer/execution/config.py`.

---

### `run_week_validation.py` — Walk-forward outcome validation

Runs the full ingest + predict pipeline for a past week of trading days, realizes outcomes, and prints a detailed prediction-vs-reality report. Use this to verify that the model has real lift before deploying or after retraining.

```bash
python3 scripts/run_week_validation.py                          # default: 5 days ending ~6 trading days ago
python3 scripts/run_week_validation.py --end 2026-03-14         # specify the last day of the window
python3 scripts/run_week_validation.py --end 2026-03-14 --days 10  # validate a 10-day window
python3 scripts/run_week_validation.py --skip-ingest            # skip ingest if data already in DB
```

The default window ends at least 6 trading days before today so all outcome horizons (3D continuation, 5D drawdown) have fully elapsed and outcomes can be realized. Passing an `--end` date closer than ~7 trading days to today will result in unrealized outcomes and an inconclusive verdict.

**What it outputs:**

1. **Per-stock per-day detail table** — classification, predicted probabilities, and realized outcomes (`CONT✓`, `DRAW✓`, max adverse excursion) for every stock in the window.
2. **Daily accuracy summary** — continuation and drawdown accuracy per day.
3. **Lift table by classification** — hit rate vs baseline for each class, sorted by lift. This is the core validity check.
4. **Verdict** — `PASS` / `FAIL` / `NEEDS MORE DATA` for whether favorable classes outperform the baseline and weak/trap classes underperform it.

**Column guide:**

| Column | Meaning |
|---|---|
| `C3D` | Predicted continuation probability (3 days) |
| `D5D` | Predicted drawdown risk (5 days) — lower is safer |
| `RISK` | Composite deception risk score — lower is safer |
| `CONT✓` | Did price actually continue in the predicted direction? |
| `DRAW✓` | Did an adverse excursion > 3% actually occur? |
| `MAE` | Max adverse excursion realized in 5 days |
| `LIFT` | Hit rate minus baseline — positive means the engine adds value |

---

## Running tests

```bash
pytest app/tests/ -v
```

Tests cover:
- Feature engineering correctness and no-leakage assertions
- Label threshold correctness and binary output validation
- Classification mapper rule branches
- Data integrity validators and market calendar
- API routes with mocked DB

---

## V1 acceptance criteria (from spec)

- [x] Daily analysis for top 30–100 active US stocks
- [x] Each stock has stable classification, calibrated probabilities, and brief interpretation
- [x] Walk-forward validation framework in place
- [x] UI-ready output structure (classification + flags + interpretation)
- [x] Daily digest payload for email/social publishing
- [x] Data pipeline is point-in-time safe

---

## V2 additions

- [x] **Intraday execution layer** (`risklayer.execution`) — rule-based WHEN/HOW decisions
  - Entry evaluation (0–30 min post-open)
  - Position management (15-minute updates with stop/target logic)
  - Portfolio rotation (compare open positions to new candidates)
  - All decisions deterministic, threshold-driven, fully transparent with reasons

---

## Execution engine — daily workflow

**Mental model.** RiskLayer (the model layer under `app/`) decides **WHAT** to trade. The execution engine under `risklayer/execution/` decides **WHEN** and **HOW** to act on that pick. The engine is rule-based, deterministic, advisory only — no broker is wired in, no LLM is in the decision path.

### Three modes

| Mode | When to run | What it answers |
|---|---|---|
| `--mode entry` | 9:30 → 10:00 ET | Should I enter the top candidate today? When? At what stop / target? |
| `--mode manage` | every 15 min while in a position | Hold, partial-exit, full-exit, or move stop? |
| `--mode portfolio` | next morning if a position is still open | Stay in the current name or rotate into today's new top pick? |

Each mode prints a banner and a structured decision (action, mode, confidence, suggested stop / TP, list of reasons with stable codes like `RECLAIM_HELD`, `EDGE_COLLAPSE`, `STRONG_BETTER`, …). Add `--json` to also write the same payload to `data/execution_decisions/YYYY-MM-DD/{ticker}.json`.

### A typical day

```bash
# 1. (Pre-market or at 9:30) — make sure today's predictions exist.
python3 scripts/run_daily.py --date $(date +%F)

# 2. (~9:30–9:35 ET) — quick gap sanity check (legacy path, optional).
python3 scripts/run_entry_check.py

# 3. (9:35 ET) — first entry evaluation.  Likely WAIT (open-noise window, 0–5 min).
python3 scripts/run_entry_check.py --mode entry --json

# 4. (9:40–10:00 ET) — re-run as the reclaim pattern develops.
python3 scripts/run_entry_check.py --mode entry
# Action will become ENTER / SKIP / WAIT depending on the intraday pattern.
# If ENTER: write the position to data/paper_positions/{TICKER}.json (see schema below).

# 5. (every 15 min while holding) — manage the open position.
python3 scripts/run_entry_check.py --mode manage \
    --paper-position-file data/paper_positions/IWM.json --json
# Watch for HOLD / REDUCE / TAKE_PROFIT_PARTIAL / TAKE_PROFIT_FULL / EXIT.

# 6. (next morning, if still holding) — compare against the new top pick.
python3 scripts/run_entry_check.py --mode portfolio \
    --paper-position-file data/paper_positions/IWM.json --json
# HOLD if Δscore < 0.08, partial ROTATE if 0.08 ≤ Δ < 0.15, strong ROTATE if Δ ≥ 0.15,
# full ROTATE if the current position is invalidated.
```

### Paper-position JSON format

`--mode manage` and `--mode portfolio` both load an open position from a small JSON file. Required keys are in **bold**:

```json
{
  "ticker": "IWM",
  "entry_price": 279.10,
  "shares": 50,
  "entry_time": "2026-05-01T13:42:00+00:00",
  "stop_loss": 277.90,
  "take_profit": 281.50,
  "mode": "CONFIRMED_RECLAIM",
  "invalidation_level": 278.20,
  "stop_at_breakeven": false,
  "entry_metrics": {
    "p_continue_5d": 0.58,
    "p_drawdown_5d": 0.27,
    "risk_score": 0.20,
    "setup_quality_score": 0.49
  }
}
```

| Field | Required | Notes |
|---|---|---|
| `ticker` | yes | Upper-cased on load |
| `entry_price` | yes | Filled price per share |
| `shares` | yes | Position size (used for unrealized-PnL display) |
| `entry_time` | yes | ISO-8601 timestamp |
| `stop_loss` | yes | Initial hard stop — if `latest_price <= stop_loss` the engine returns `EXIT` |
| `take_profit` | yes | If `latest_price >= take_profit` the engine returns `TAKE_PROFIT_FULL` |
| `mode` | optional | `OPEN_ENTRY` (default), `CONFIRMED_RECLAIM`, or `SWING_HOLD` |
| `invalidation_level` | optional | Price below which the thesis dies; defaults to `stop_loss` |
| `stop_at_breakeven` | optional | Set to `true` once you've moved the stop to entry — suppresses the reminder |
| `entry_metrics` | optional but strongly recommended | The RiskLayer metrics captured at entry — used to detect edge decay (`p_continue_5d` drop, `p_drawdown_5d` rise). Without these the engine cannot suggest `REDUCE`. |

There is no broker call. To "execute" the engine's recommendation you act manually (paper or real) and update the JSON file accordingly — e.g. set `stop_at_breakeven: true` after moving your stop, raise `stop_loss` after a partial take-profit, or delete the file once you're flat.

### Threshold reference (defaults from `risklayer/execution/config.py`)

| Threshold | Default | Used by |
|---|---|---|
| `open_noise_minutes` | 5 | Entry — first-N-minutes WAIT window |
| `entry_window_minutes` | 30 | Entry — outside this window, decisions are flagged |
| `max_open_gap_atr` | 0.8 | Entry — `\|open - prev_close\| / ATR ≥ this → SKIP` |
| `max_current_extension_atr` | 0.8 | Entry — `\|latest - open\| / ATR ≥ this → SKIP/WAIT` |
| `reclaim_hold_candles` | 2 | Entry — consecutive 1m closes above the reclaim level |
| `reduce_p_continue_drop` | 0.08 | Manage — REDUCE 30% if `p_continue_5d` drops by ≥ this |
| `reduce_drawdown_rise` | 0.10 | Manage — REDUCE 30% if `p_drawdown_5d` rises by ≥ this |
| `exit_p_continue_threshold` | 0.50 | Manage — EXIT if both edge thresholds are breached |
| `exit_drawdown_threshold` | 0.40 | Manage — EXIT if both edge thresholds are breached |
| `breakeven_r_multiple` | 1.0 | Manage — at this R, suggest moving stop to break-even |
| `partial_tp_r_multiple` | 1.5 | Manage — at this R, allow `TAKE_PROFIT_PARTIAL` (40%) |
| `rotation_score_delta` | 0.08 | Portfolio — minimum score Δ to consider rotating |
| `strong_rotation_score_delta` | 0.15 | Portfolio — Δ that justifies a 50–70% rotate |

Override per-call by constructing an `ExecutionConfig(...)` and passing it to `evaluate_entry / manage_position / evaluate_rotation`.

---

## Explicitly out of scope

- News / NLP ingestion
- Options activity context
- LLM-generated interpretations for trade decisions
- Portfolio-level optimization
- Mobile app
- Broker integrations (handle externally)
