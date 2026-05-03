# `risklayer.execution` — Intraday Execution Engine

> **Design principle:** RiskLayer (the model layer under `app/`) decides
> **WHAT** to trade. This package decides **WHEN** and **HOW** to act.

The execution engine is a rule-based, deterministic layer that consumes
RiskLayer model output and returns structured trading decisions for the
intraday lifecycle: entry timing, position management, and portfolio
rotation.

## Boundaries — read this first

- **Advisory only.** The engine prints / returns recommendations as
  structured `ExecutionDecision` objects. **It never places broker
  orders or moves money.** "Executing" a recommendation means you act
  manually (paper or real) and then update your paper-position JSON.
- **No LLM in the decision path.** Every output is the result of pure
  Python rule evaluation against the thresholds in
  [`config.py`](./config.py). LLMs do not invent or veto trades.
- **Deterministic.** Same inputs → same decision, every time.
- **Structured.** Every decision carries a list of `DecisionReason`
  records with stable codes (`GAP_TOO_LARGE`, `RECLAIM_HELD`,
  `EDGE_COLLAPSE`, `STRONG_BETTER`, …) so downstream tooling can branch
  on them.

## Quick start

```python
import datetime as dt
from risklayer.execution import (
    evaluate_entry,
    manage_position,
    evaluate_rotation,
    ExecutionAction,
    TradeMode,
)
from risklayer.execution.position_manager import OpenPosition

# 1. Entry evaluation (between 9:30 and 10:00 ET).
entry_decision = evaluate_entry(
    candidate={
        "ticker": "IWM",
        "p_continue_5d": 0.58,
        "p_drawdown_5d": 0.27,
        "risk_score": 0.20,
        "setup_quality_score": 0.49,
    },
    candles_1m=candles_1m,
    candles_5m=candles_5m,
    atr=4.32,
    open_price=278.66,
    prev_close=278.20,
    latest_price=279.10,
)

if entry_decision.action == ExecutionAction.ENTER:
    print(entry_decision.suggested_entry, entry_decision.suggested_stop,
          entry_decision.conservative_take_profit, entry_decision.swing_take_profit)

# 2. Position management (every 15 minutes during the session).
position = OpenPosition(
    ticker="IWM",
    entry_price=279.10,
    shares=50,
    entry_time=dt.datetime.now(dt.timezone.utc),
    stop_loss=277.90,
    take_profit=281.50,
    mode=TradeMode.CONFIRMED_RECLAIM,
    invalidation_level=278.20,
    entry_metrics={"p_continue_5d": 0.58, "p_drawdown_5d": 0.27},
)
mgmt = manage_position(position, latest_price=279.80, candles_15m=candles_15m,
                       latest_metrics={"p_continue_5d": 0.50, "p_drawdown_5d": 0.38})
print(mgmt.action, mgmt.reduce_percent)

# 3. Portfolio rotation (next morning, when a new candidate appears).
rotation = evaluate_rotation(
    current_ticker="IWM",
    current_metrics={"p_continue_5d": 0.55, "p_drawdown_5d": 0.30,
                     "risk_score": 0.30, "setup_quality_score": 0.40},
    new_candidate_ticker="XLY",
    new_candidate_metrics={"p_continue_5d": 0.68, "p_drawdown_5d": 0.22,
                           "risk_score": 0.20, "setup_quality_score": 0.55},
)
print(rotation.decision.action, rotation.decision.rotate_to,
      rotation.score_delta)
```

## Public API

### `evaluate_entry(candidate, *, candles_1m, candles_5m=None, atr, open_price, prev_close, latest_price=None, market_open=None, config=None)`

Returns `ExecutionDecision` with action `WAIT` / `ENTER` / `SKIP`.

Cascade (top-to-bottom; first match wins):

1. Invalid ATR / prices → `SKIP`.
2. No 1m candles yet → `WAIT` (`NO_INTRADAY_DATA`).
3. `|open - prev_close| / ATR ≥ max_open_gap_atr` (default **0.8**) →
   `SKIP` with `GAP_TOO_LARGE`.
4. `|latest - open| / ATR ≥ max_current_extension_atr` (default
   **0.8**) → `SKIP` (chasing) or `WAIT` (flushed).
5. **First 5 minutes** (`open_noise_minutes = 5`) → `WAIT` with
   `OPEN_NOISE_WINDOW`.
6. Pattern detection inside the **5–30 minute** window
   (`entry_window_minutes = 30`):
   - **Invalid first** → `WAIT`: `FAILED_BREAKOUT`,
     `UPPER_WICK_REJECTION`, `FIRST_BOUNCE_NO_BASE`.
   - **Valid** → `ENTER`: `FIVE_MIN_CONFIRMATION`,
     `PULLBACK_RECLAIM`, `RECLAIM_HELD`.

`ENTER` returns one of two `TradeMode`s:
- `OPEN_ENTRY` — ATR stop (1 ATR), 2 ATR target.
- `CONFIRMED_RECLAIM` — structure stop (just below the reclaim level
  but never tighter than 0.4 ATR), conservative TP at 2R, plus a
  `swing_take_profit` scaled by RiskLayer's `p_continue_5d`.

### `manage_position(position, *, latest_price, candles_15m=None, latest_metrics=None, config=None)`

Decisions for an open position. Priority order:

1. `latest_price <= stop_loss` → `EXIT` with `STOP_HIT`.
2. `latest_price >= take_profit` → `TAKE_PROFIT_FULL`.
3. RiskLayer edge collapse (`p_continue_5d <= 0.50` AND
   `p_drawdown_5d >= 0.40`) → `EXIT` with `EDGE_COLLAPSE`. Forces an
   exit even in `SWING_HOLD` mode.
4. 15m close below `invalidation_level` → `EXIT`
   (`INVALIDATION_BREAK`). `SWING_HOLD` ignores this when the model
   edge has not also collapsed (`SWING_IGNORE_NOISE`).
5. ≥ 1.5R reached + momentum weakening → `TAKE_PROFIT_PARTIAL` (~40%).
6. `p_continue_5d` drop ≥ 0.08 → `REDUCE` 30%.
   `p_drawdown_5d` rise ≥ 0.10 → `REDUCE` 30%.
   Both at once → `REDUCE` 50%.
7. ≥ 1R reached → annotate "move stop to break-even" while keeping
   the action as `HOLD`.
8. Otherwise → `HOLD`.

### `evaluate_rotation(*, current_ticker, current_metrics, new_candidate_ticker, new_candidate_metrics, current_position_invalidated=False, available_capital=None, config=None)`

Returns `PortfolioDecision` with both scores attached. Score formula:

```
score = 0.45 * p_continue_5d
      + 0.30 * (1 - p_drawdown_5d)
      + 0.20 * (1 - risk_score)
      + 0.05 * setup_quality_score
```

| Situation | Action |
|---|---|
| Same ticker | `HOLD` (`SAME_TICKER`) |
| Δ < `rotation_score_delta` (0.08) | `HOLD` (`DELTA_BELOW_THRESHOLD`) |
| `current_position_invalidated=True` | `ROTATE` 100% (`CURRENT_INVALIDATED`) |
| Δ ≥ `strong_rotation_score_delta` (0.15) | `ROTATE` 50–70% (`STRONG_BETTER`) |
| Δ ≥ 0.08 (modest) | `ROTATE` 30–50% (`MODEST_BETTER`) |
| Δ just barely ≥ 0.08 (< 0.10) | `ROTATE` 20% (`MINOR_BETTER`) |

Full exits require `current_position_invalidated=True`. The engine
never rotates 100% on a score difference alone.

## Decision objects

```python
@dataclass
class ExecutionDecision:
    ticker: str
    action: ExecutionAction
    mode: Optional[TradeMode]
    confidence: float                    # 0.0–1.0

    suggested_entry: Optional[float]
    suggested_stop: Optional[float]
    suggested_take_profit: Optional[float]
    conservative_take_profit: Optional[float]   # 2R
    swing_take_profit: Optional[float]          # RiskLayer ATR target

    reduce_percent: Optional[float]      # for REDUCE / TAKE_PROFIT_PARTIAL / ROTATE
    rotate_to: Optional[str]             # for ROTATE
    invalidation_level: Optional[float]

    reasons: list[DecisionReason]
    timestamp: dt.datetime

@dataclass(frozen=True)
class DecisionReason:
    code: str       # stable identifier (e.g. "RECLAIM_HELD")
    severity: Severity   # INFO / POSITIVE / WARNING / NEGATIVE / CRITICAL
    message: str    # human-readable
```

`ExecutionAction` values: `WAIT`, `ENTER`, `SKIP`, `HOLD`, `REDUCE`,
`EXIT`, `ROTATE`, `TAKE_PROFIT_PARTIAL`, `TAKE_PROFIT_FULL`.

`TradeMode` values: `OPEN_ENTRY`, `CONFIRMED_RECLAIM`, `SWING_HOLD`.

`decision.to_dict()` produces a JSON-safe payload — used by the
`--json` flag on `scripts/run_entry_check.py` to write
`data/execution_decisions/YYYY-MM-DD/{ticker}.json`.

## Front-end script — three modes

`scripts/run_entry_check.py` is the daily entry point.

```bash
# Entry mode — pre/at-open candidate analysis (0–30 min entry timing).
python3 scripts/run_entry_check.py --mode entry [--ticker SYM]

# Manage mode — 15-minute check on an open paper position.
python3 scripts/run_entry_check.py --mode manage \
    --paper-position-file data/paper_positions/IWM.json

# Portfolio mode — held position vs today's top RiskLayer candidate.
python3 scripts/run_entry_check.py --mode portfolio \
    --paper-position-file data/paper_positions/IWM.json

# Add --json to also write the decision to
# data/execution_decisions/YYYY-MM-DD/{ticker}.json
```

The legacy single-day check and `--backtest` paths are preserved
(omit `--mode`).

## Paper-position JSON format

`--mode manage` and `--mode portfolio` both load an open position from
a JSON file. Required keys are in **bold**.

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
| `shares` | yes | Used for unrealized-PnL display |
| `entry_time` | yes | ISO-8601 timestamp |
| `stop_loss` | yes | If `latest_price <= stop_loss` → `EXIT` |
| `take_profit` | yes | If `latest_price >= take_profit` → `TAKE_PROFIT_FULL` |
| `mode` | optional | `OPEN_ENTRY` (default), `CONFIRMED_RECLAIM`, `SWING_HOLD` |
| `invalidation_level` | optional | Defaults to `stop_loss` |
| `stop_at_breakeven` | optional | Set to `true` after you've moved your stop |
| `entry_metrics` | optional but strongly recommended | RiskLayer metrics at entry — required to detect `REDUCE` triggers |

There is no broker call. To "execute" the recommendation you act
manually (paper or real) and update the JSON: e.g. set
`stop_at_breakeven: true` after moving your stop, raise `stop_loss`
after a partial take-profit, or delete the file once you're flat.

## Configurable thresholds

All defaults live in [`config.py`](./config.py).

| Threshold | Default | Used by |
|---|---|---|
| `open_noise_minutes` | 5 | Entry — first-N-minutes WAIT |
| `entry_window_minutes` | 30 | Entry — flag decisions outside this window |
| `max_open_gap_atr` | **0.8** | Entry — gap-vs-ATR SKIP cutoff |
| `max_current_extension_atr` | **0.8** | Entry — extension-vs-ATR cutoff |
| `reclaim_hold_candles` | 2 | Entry — closes above reclaim level |
| `reclaim_max_upper_wick_ratio` | 0.55 | Entry — wick rejection sensitivity |
| `reclaim_max_extension_atr` | 0.5 | Entry — max extension above open for reclaim |
| `reduce_p_continue_drop` | 0.08 | Manage — `REDUCE` on edge decay |
| `reduce_drawdown_rise` | 0.10 | Manage — `REDUCE` on rising drawdown risk |
| `reduce_single_percent` | 0.30 | Manage — single-trigger reduce size |
| `reduce_combined_percent` | 0.50 | Manage — both triggers reduce size |
| `weak_p_continue_threshold` | 0.52 | Manage — standalone weak-edge cutoff |
| `weak_drawdown_threshold` | 0.35 | Manage — standalone weak-edge cutoff |
| `exit_p_continue_threshold` | 0.50 | Manage — EXIT when paired with drawdown |
| `exit_drawdown_threshold` | 0.40 | Manage — EXIT when paired with `p_continue` |
| `breakeven_r_multiple` | 1.0 | Manage — annotate move-stop-to-BE |
| `partial_tp_r_multiple` | 1.5 | Manage — `TAKE_PROFIT_PARTIAL` allowed |
| `partial_tp_size` | 0.40 | Manage — partial size (40%) |
| `score_w_p_continue` | 0.45 | Portfolio — score weight |
| `score_w_drawdown` | 0.30 | Portfolio — score weight |
| `score_w_risk` | 0.20 | Portfolio — score weight |
| `score_w_setup_quality` | 0.05 | Portfolio — score weight |
| `rotation_score_delta` | 0.08 | Portfolio — minimum Δ to rotate |
| `strong_rotation_score_delta` | 0.15 | Portfolio — Δ for 50–70% rotate |

Override per-call:

```python
from risklayer.execution.config import DEFAULT_CONFIG

cfg = DEFAULT_CONFIG.with_overrides(
    max_open_gap_atr=0.6,        # tighter gap rejection
    reduce_p_continue_drop=0.10, # less sensitive REDUCE
)
decision = evaluate_entry(candidate, ..., config=cfg)
```

## Data structures (`_candles.py`)

```python
@dataclass(frozen=True)
class Candle:
    timestamp: dt.datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
```

`to_candles(df)` converts a pandas DataFrame (Open/High/Low/Close/Volume +
DatetimeIndex) to `list[Candle]` — the only place we touch pandas in
the engine, so the rule modules stay easy to unit-test with synthetic
candles.

## Tests

```bash
pytest -q app/tests/test_execution_engine.py
```

22 tests cover every entry / manage / portfolio scenario from the
spec. No DB, no network, no yfinance — all candles are synthesized in
the tests.

## Out of scope (intentionally)

- Placing broker orders or transferring funds. **Advisory only.**
- LLM-driven trade invention or veto.
- Portfolio-level optimization (sizing, correlation, drawdown caps
  across positions). Single-position discipline only.
- Re-ranking or re-classifying candidates — that is RiskLayer's job
  (`app/services/ranking_service.py`,
  `app/classification/mapper.py`). The engine consumes RiskLayer's
  output and decides timing / exit only.
