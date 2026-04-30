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

### `run_entry_check.py` — Morning entry check

Run this at 9:30–9:35 AM ET on any live trading day to evaluate whether the model's top continuation candidate has a clean entry at the open.

```bash
python3 scripts/run_entry_check.py                    # default: today's predictions
python3 scripts/run_entry_check.py --date 2024-11-15  # evaluate a specific past day
```

**What it does:**

Loads the top continuation candidate from the specified date's model predictions (via `get_top_continuation`). Downloads recent OHLCV and live intraday data via yfinance, computes 14-day ATR, and prints two verdicts:

- `OPEN ENTRY` — was the open-price entry clean relative to ATR?
- `CURRENT` — is the current price still a valid entry right now?

**Verdict thresholds (gap / ATR):**

| Ratio | Verdict | Meaning |
|---|---|---|
| < 0.5x | `BUY / CLEAN ENTRY` | Entry is within normal range; edge intact |
| 0.5–1.0x | `CAUTION` | Gap is marginal; consider reducing size or waiting |
| ≥ 1.0x | `SKIP` | Move too extreme; model edge is gone for today |

If no stock passes the minimum signal threshold (`p_continue_3d ≥ 0.45`, favorable classification), the script prints `NO SETUP TODAY` and exits. Do not substitute a neutral stock — wait for the next session.

When the open verdict is `BUY`, the script also prints a suggested stop (1 ATR from open) and a 3-day target (2 ATR from open).

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

## Explicitly out of scope for V1

- Intraday mode
- News / NLP ingestion
- Options activity context
- LLM-generated interpretations
- Portfolio-level layer
- Mobile app
- Broker integrations
