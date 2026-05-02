# risklayer.execution — Intraday Trading Execution Engine

> **Design principle:** RiskLayer (the model) decides **WHAT** to trade. This package decides **WHEN** and **HOW** to act.

The execution engine is a rule-based, deterministic layer that sits on top of RiskLayer model output and generates structured trading decisions for the entire intraday lifecycle: entry validation, position management, and portfolio rotation.

All decisions are fully threshold-driven (no LLM), return transparent `ExecutionDecision` objects with human-readable reasons, and can be serialized to JSON for logging and downstream tooling.

---

## Quick start

```python
from risklayer.execution import (
    evaluate_entry,
    manage_position,
    evaluate_rotation,
    ExecutionDecision,
    ExecutionAction,
    TradeMode,
    OpenPosition,
)

# 1. Entry evaluation (at 9:30am, during first 30 min)
entry_decision = evaluate_entry(
    candidate={"ticker": "NVDA", "p_continue_5d": 0.72, "risk_score": 0.3},
    candles_1m=candles_1m,
    atr=2.5,
    open_price=120.50,
    prev_close=119.80,
    latest_price=120.40,
)

if entry_decision.action == ExecutionAction.ENTER:
    print(f"Entry: {entry_decision.suggested_entry} | Stop: {entry_decision.suggested_stop}")

# 2. Position management (every 15 min during session)
position = OpenPosition(
    ticker="NVDA",
    entry_price=120.60,
    shares=100,
    entry_time=dt.datetime.now(),
    stop_loss=118.10,
    take_profit=125.00,
)

mgmt_decision = manage_position(position, latest_price=123.50, candles_15m=candles_15m)
print(f"Action: {mgmt_decision.action}")

# 3. Rotation decision (when new candidate appears)
rotation = evaluate_rotation(
    current_position=position,
    candidate={"ticker": "TSLA", "p_continue_5d": 0.85, "risk_score": 0.2},
    latest_price=123.50,
)
print(f"Rotate? {rotation.decision.action == ExecutionAction.ROTATE}")
```

---

## Core concepts

### ExecutionDecision

Every function returns an `ExecutionDecision` with:

```python
@dataclass
class ExecutionDecision:
    ticker: str
    action: ExecutionAction  # WAIT, ENTER, SKIP, HOLD, REDUCE, EXIT, ROTATE, etc.
    mode: TradeMode          # OPEN_ENTRY, CONFIRMED_RECLAIM, SWING_HOLD
    confidence: float        # 0.0–1.0
    
    suggested_entry: Optional[float]              # Entry price
    suggested_stop: Optional[float]               # Stop loss
    suggested_take_profit: Optional[float]        # Primary target
    conservative_take_profit: Optional[float]     # Conservative target
    swing_take_profit: Optional[float]            # Multi-day target
    
    reduce_percent: Optional[float]               # For REDUCE actions (0.0–1.0)
    rotate_to: Optional[str]                      # For ROTATE actions
    invalidation_level: Optional[float]           # Thesis invalidation price
    
    reasons: list[DecisionReason]                 # Transparent decision tree
    timestamp: dt.datetime                        # When decision was made
```

Each reason has:
- **code** (str) — machine-readable identifier for branching (e.g., `"GAP_TOO_LARGE"`, `"STOP_HIT"`)
- **severity** (Severity) — INFO, POSITIVE, WARNING, NEGATIVE, CRITICAL
- **message** (str) — human-readable explanation

---

## Public API

### 1. `evaluate_entry()` — Entry validation (0–30 min post-open)

**Purpose:** Confirm whether a RiskLayer candidate should be entered at the current price/time.

**Signature:**
```python
def evaluate_entry(
    candidate: Mapping[str, Any],
    *,
    candles_1m: Sequence[Candle],
    candles_5m: Optional[Sequence[Candle]] = None,
    atr: float,
    open_price: float,
    prev_close: float,
    latest_price: Optional[float] = None,
    market_open: Optional[dt.datetime] = None,
    config: Optional[ExecutionConfig] = None,
) -> ExecutionDecision:
```

**Inputs:**

| Parameter | Type | Required | Description |
|---|---|---|---|
| `candidate` | dict-like | Yes | RiskLayer output: `ticker`, `p_continue_5d`, `p_drawdown_5d`, `risk_score`, `setup_quality_score` |
| `candles_1m` | list[Candle] | Yes | Intraday 1-minute bars (timestamp, open, high, low, close, volume) |
| `candles_5m` | list[Candle] | No | Intraday 5-minute bars (optional, used for SMA/volume context) |
| `atr` | float | Yes | Prior-session 14-day ATR (price units) |
| `open_price` | float | Yes | Today's official market open price |
| `prev_close` | float | Yes | Prior session's close price |
| `latest_price` | float | No | Current price (defaults to last 1m candle close) |
| `market_open` | datetime | No | Market open timestamp (defaults to first 1m candle timestamp) |
| `config` | ExecutionConfig | No | Custom thresholds; uses defaults if None |

**Returns:** `ExecutionDecision` with action = WAIT / ENTER / SKIP

**Entry rule cascade** (top-to-bottom; first match stops evaluation):

1. **Invalid ATR/prices** → SKIP (CRITICAL)
2. **No intraday data yet** → WAIT (WARNING, come back in a few minutes)
3. **Gap too large** (≥ 2.5x ATR) → SKIP (CRITICAL, model edge is gone)
4. **Price moved too far from open** (> 1.5%) → SKIP (CRITICAL, missed the setup)
5. **No reclaim of overnight structure** (if down open, body still below prev close) → SKIP (WARNING)
6. **Volume + candle health** (SMA20 > SMA50 on 5m, healthy candle close) → advances confidence
7. **Confidence math** (blend RiskLayer probs + intraday patterns) → confidence score

If all rules pass and confidence ≥ threshold:
- **Action:** ENTER
- **Mode:** OPEN_ENTRY (for ATR-based stops) or CONFIRMED_RECLAIM (for structure stops)
- **Targets:** 2–4 ATRs from entry (configurable)
- **Stop:** 1 ATR below entry (or structure-based)

**Example:**
```python
decision = evaluate_entry(
    {"ticker": "NVDA", "p_continue_5d": 0.72, "risk_score": 0.25, "setup_quality_score": 0.8},
    candles_1m=candles_1m,
    candles_5m=candles_5m,
    atr=2.3,
    open_price=120.50,
    prev_close=119.80,
    latest_price=120.40,
)

for reason in decision.reasons:
    print(f"  {reason.code:20s} [{reason.severity.value:8s}] {reason.message}")
# Output example:
#   OPEN_GAP          [INFO    ] Gap is 0.70 / 2.3 = 0.30x ATR (< 2.5); edge intact
#   VOLUME_CHECK      [POSITIVE] 5m SMA20 > SMA50; volume healthy
#   CONFIDENCE_OK     [POSITIVE] Confidence 0.68 (> 0.55 threshold); ready to enter
```

---

### 2. `manage_position()` — Position lifecycle (15-minute updates)

**Purpose:** Given an open position, decide whether to HOLD, REDUCE, EXIT, or TAKE_PROFIT at each update.

**Signature:**
```python
def manage_position(
    position: OpenPosition,
    *,
    latest_price: float,
    candles_15m: Optional[Sequence[Candle]] = None,
    latest_metrics: Optional[Mapping[str, Any]] = None,
    config: Optional[ExecutionConfig] = None,
) -> ExecutionDecision:
```

**Inputs:**

| Parameter | Type | Description |
|---|---|---|
| `position` | OpenPosition | Snapshot of the open trade (entry, stop, target, mode, entry_metrics) |
| `latest_price` | float | Current market price |
| `candles_15m` | list[Candle] | Recent 15-minute bars (optional, for intraday structure) |
| `latest_metrics` | dict | Updated RiskLayer metrics (optional, for edge decay detection) |
| `config` | ExecutionConfig | Custom thresholds (optional) |

**OpenPosition dataclass:**
```python
@dataclass
class OpenPosition:
    ticker: str
    entry_price: float
    shares: float
    entry_time: dt.datetime
    stop_loss: float
    take_profit: float
    mode: TradeMode = TradeMode.OPEN_ENTRY
    invalidation_level: Optional[float] = None  # Price below which thesis dies
    entry_metrics: Mapping[str, Any] = {}       # RiskLayer metrics at entry time
    stop_at_breakeven: bool = False             # Already moved stop to breakeven
    
    @property
    def initial_risk_per_share(self) -> float:
        """Distance from entry to stop (always positive)."""
        return max(self.entry_price - self.stop_loss, 0.0)
    
    def r_multiple(self, price: float) -> float:
        """How many R the position is currently up/down."""
        risk = self.initial_risk_per_share
        return (price - self.entry_price) / risk if risk > 0 else 0.0
```

**Management rule cascade:**

1. **Stop loss hit** → EXIT (CRITICAL)
2. **Invalidation level breached** → EXIT (CRITICAL, thesis broken)
3. **Edge decay detected** (new metrics significantly worse) → REDUCE or EXIT (WARNING/NEGATIVE)
4. **Take profit tier 1 hit** (≥ +1.5R) → TAKE_PROFIT_PARTIAL (50% default)
5. **Take profit tier 2 hit** (≥ +3.0R) → TAKE_PROFIT_FULL (100%)
6. **Stop moved to breakeven** (optional, after +0.75R) → updates stop, HOLD
7. **Otherwise** → HOLD with updated reasons

**Returns:** `ExecutionDecision` with action = HOLD / REDUCE / EXIT / TAKE_PROFIT_PARTIAL / TAKE_PROFIT_FULL

**Example:**
```python
position = OpenPosition(
    ticker="NVDA",
    entry_price=120.60,
    shares=100,
    entry_time=dt.datetime.now(dt.timezone.utc),
    stop_loss=118.10,
    take_profit=125.00,
    mode=TradeMode.OPEN_ENTRY,
    entry_metrics={"p_continue_5d": 0.72, "risk_score": 0.25},
)

# At current price 123.50
decision = manage_position(position, latest_price=123.50)
print(f"R multiple: {position.r_multiple(123.50):.2f}R")
# R multiple: 1.22R (profit of 1.22x the risk)

# If we also have new RiskLayer metrics showing edge decay:
new_metrics = {"p_continue_5d": 0.52, "risk_score": 0.45}  # Degraded
decision = manage_position(
    position,
    latest_price=123.50,
    latest_metrics=new_metrics,
)
# decision.action might be REDUCE if metrics have decayed significantly
```

---

### 3. `evaluate_rotation()` — Portfolio rotation decision

**Purpose:** Compare an open position against a new RiskLayer candidate and decide whether to ROTATE or HOLD.

**Signature:**
```python
def evaluate_rotation(
    current_position: OpenPosition,
    candidate: Mapping[str, Any],
    *,
    latest_price: float,
    invalidated: bool = False,
    current_metrics: Optional[Mapping[str, Any]] = None,
    config: Optional[ExecutionConfig] = None,
) -> PortfolioDecision:
```

**Inputs:**

| Parameter | Type | Description |
|---|---|---|
| `current_position` | OpenPosition | The open position being evaluated |
| `candidate` | dict | New RiskLayer output (ticker, p_continue_5d, p_drawdown_5d, risk_score, setup_quality_score) |
| `latest_price` | float | Current price of the open position |
| `invalidated` | bool | Whether the original thesis has broken (default False) |
| `current_metrics` | dict | Optional RiskLayer metrics for the current position (for scoring) |
| `config` | ExecutionConfig | Custom thresholds (optional) |

**Returns:** `PortfolioDecision` with decision.action = ROTATE / HOLD

**Scoring formula:**
```
candidate_score = 
    0.45 * p_continue_5d 
  + 0.30 * (1 - p_drawdown_5d) 
  + 0.20 * (1 - risk_score) 
  + 0.05 * setup_quality_score
```

Higher score = better candidate. Missing fields default to worst-case (0 for "good" metrics, 1 for "bad").

**Rotation logic:**
1. Compute score for current position (from entry_metrics)
2. Compute score for new candidate
3. Calculate delta: new_score - current_score
4. Gate decisions:
   - If invalidated AND new_score > current_score → **ROTATE** (exit old, enter new)
   - If NOT invalidated AND delta > rotation_score_delta (default 0.05) → **ROTATE**
   - If delta > strong_rotation_score_delta (default 0.15) → **ROTATE** (override invalidation)
   - Otherwise → **HOLD** (keep current position)

**Example:**
```python
position = OpenPosition(
    ticker="NVDA",
    entry_price=120.60,
    shares=100,
    entry_time=dt.datetime.now(dt.timezone.utc),
    stop_loss=118.10,
    take_profit=125.00,
    entry_metrics={"p_continue_5d": 0.72, "p_drawdown_5d": 0.15, "risk_score": 0.25, "setup_quality_score": 0.8},
)

# New candidate appears
new_candidate = {
    "ticker": "TSLA",
    "p_continue_5d": 0.85,
    "p_drawdown_5d": 0.10,
    "risk_score": 0.20,
    "setup_quality_score": 0.9,
}

rotation = evaluate_rotation(position, new_candidate, latest_price=123.50)
print(f"Current score: {rotation.current_score:.3f}")
print(f"New score: {rotation.new_score:.3f}")
print(f"Delta: {rotation.score_delta:.3f}")
print(f"Action: {rotation.decision.action}")

# Output example:
# Current score: 0.524
# New score: 0.654
# Delta: 0.130
# Action: ROTATE (delta > 0.05 threshold)
```

---

## Configuration

All thresholds are centralized in `ExecutionConfig` and can be overridden per-call.

```python
from risklayer.execution.config import get_config, ExecutionConfig

# Load defaults
cfg = get_config()

# Access thresholds
print(cfg.max_open_gap_atr)               # 2.5
print(cfg.max_move_from_open_pct)         # 1.5
print(cfg.rotation_score_delta)           # 0.05
print(cfg.strong_rotation_score_delta)    # 0.15

# Custom config
custom_cfg = ExecutionConfig(
    max_open_gap_atr=3.0,
    max_move_from_open_pct=2.0,
    take_profit_r_tiers=[1.5, 3.0, 5.0],
)

decision = evaluate_entry(..., config=custom_cfg)
```

**Key config fields:**

| Field | Default | Meaning |
|---|---|---|
| `max_open_gap_atr` | 2.5 | Max gap size (in ATRs) before SKIP |
| `max_move_from_open_pct` | 1.5 | Max price move from open (%) before SKIP |
| `require_reclaim` | True | Require overnight body reclaim before ENTER |
| `atr_multiple_stop` | 1.0 | Stop placement: N × ATR below entry |
| `take_profit_r_tiers` | [1.5, 3.0] | R-multiple thresholds for TAKE_PROFIT |
| `take_profit_reduce_pct` | [0.5, 1.0] | Percent to reduce at each tier |
| `rotation_score_delta` | 0.05 | Min score improvement to rotate (if not invalidated) |
| `strong_rotation_score_delta` | 0.15 | Min score improvement to force rotation |
| `score_w_p_continue` | 0.45 | Weight on p_continue_5d |
| `score_w_drawdown` | 0.30 | Weight on (1 - p_drawdown_5d) |
| `score_w_risk` | 0.20 | Weight on (1 - risk_score) |
| `score_w_setup_quality` | 0.05 | Weight on setup_quality_score |

---

## Data structures

### Candle

```python
@dataclass
class Candle:
    timestamp: dt.datetime
    open: float
    high: float
    low: float
    close: float
    volume: int  # Can be 0 for synthetic candles
```

### TradeMode

```python
class TradeMode(str, Enum):
    OPEN_ENTRY = "OPEN_ENTRY"              # Entered at open, ATR-based stop
    CONFIRMED_RECLAIM = "CONFIRMED_RECLAIM"  # Entered after reclaim, structure stop
    SWING_HOLD = "SWING_HOLD"              # Multi-day, ignore intraday noise
```

### ExecutionAction

```python
class ExecutionAction(str, Enum):
    WAIT = "WAIT"                          # Come back later (intraday only)
    ENTER = "ENTER"                        # Entry is valid, execute now
    SKIP = "SKIP"                          # Don't enter this candidate today
    HOLD = "HOLD"                          # Position is good, keep holding
    REDUCE = "REDUCE"                      # Trim position (specify reduce_percent)
    EXIT = "EXIT"                          # Close full position
    ROTATE = "ROTATE"                      # Exit old, enter new (specify rotate_to)
    TAKE_PROFIT_PARTIAL = "TAKE_PROFIT_PARTIAL"  # Hit target, take 50% off
    TAKE_PROFIT_FULL = "TAKE_PROFIT_FULL"        # Hit full target, close position
```

### Severity

```python
class Severity(str, Enum):
    INFO = "INFO"                   # Informational (e.g., "No data yet")
    POSITIVE = "POSITIVE"          # Supports the action
    WARNING = "WARNING"            # Cautionary but not decisive
    NEGATIVE = "NEGATIVE"          # Works against the action
    CRITICAL = "CRITICAL"          # Dominates the action (e.g., stop hit)
```

---

## Integration patterns

### Daily trading loop

```python
import datetime as dt
from risklayer.execution import evaluate_entry, manage_position

# 9:30 AM: Evaluate candidates
for candidate in today_risklayer_candidates:
    decision = evaluate_entry(
        candidate,
        candles_1m=fetch_1m_candles(candidate['ticker']),
        atr=compute_atr(candidate['ticker']),
        open_price=get_open(candidate['ticker']),
        prev_close=get_prev_close(candidate['ticker']),
    )
    
    if decision.action == ExecutionAction.ENTER:
        position = execute_entry(candidate['ticker'], decision)
        store_position(position)

# 10:00 AM, 10:15 AM, ... 3:45 PM: Manage open positions
for position in active_positions:
    decision = manage_position(
        position,
        latest_price=get_latest_price(position.ticker),
        candles_15m=fetch_15m_candles(position.ticker),
        latest_metrics=get_latest_risklayer_metrics(position.ticker),
    )
    
    execute_management_action(decision)
    update_position_state(position, decision)

# 4:00 PM: Check rotations before close
for position in active_positions:
    best_new_candidate = find_next_best_candidate(position.ticker)
    rotation = evaluate_rotation(position, best_new_candidate, latest_price=...)
    
    if rotation.decision.action == ExecutionAction.ROTATE:
        exit_and_rotate(position, rotation.decision)
```

---

## Testing

```python
import pytest
from risklayer.execution import evaluate_entry, ExecutionAction, Candle

def test_enter_clean_setup():
    """Test a clean entry scenario."""
    candles_1m = [
        Candle(dt.datetime(...), 120.0, 120.5, 119.9, 120.3, 100000),
        # ... more candles
    ]
    
    decision = evaluate_entry(
        {"ticker": "NVDA", "p_continue_5d": 0.72, "risk_score": 0.3},
        candles_1m=candles_1m,
        atr=2.0,
        open_price=120.0,
        prev_close=119.5,
    )
    
    assert decision.action == ExecutionAction.ENTER
    assert decision.suggested_stop < decision.suggested_entry
    assert len(decision.reasons) > 0

def test_skip_on_large_gap():
    """Test that large gaps trigger SKIP."""
    decision = evaluate_entry(
        {"ticker": "NVDA", "p_continue_5d": 0.72},
        candles_1m=[...],
        atr=2.0,
        open_price=125.0,  # Gap too large
        prev_close=119.5,
    )
    
    assert decision.action == ExecutionAction.SKIP
    gap_reason = [r for r in decision.reasons if r.code == "GAP_TOO_LARGE"]
    assert len(gap_reason) == 1
```

---

## Error handling

All functions degrade gracefully:
- Missing `p_continue_5d` → treated as 0.0 (worst-case)
- Missing `risk_score` → treated as 1.0 (worst-case)
- Empty `candles_1m` → action = WAIT (not ERROR)
- Non-positive ATR → action = SKIP (not ERROR)

No exceptions are raised during normal operation. All decision-making happens via the reason chain.

---

## Serialization

All decisions can be serialized to JSON:

```python
decision = evaluate_entry(...)
json_dict = decision.to_dict()
json_str = json.dumps(json_dict, indent=2)

# Output example:
# {
#   "ticker": "NVDA",
#   "action": "ENTER",
#   "mode": "OPEN_ENTRY",
#   "confidence": 0.6823,
#   "suggested_entry": 120.50,
#   "suggested_stop": 118.10,
#   "suggested_take_profit": 125.00,
#   "reasons": [
#     {
#       "code": "OPEN_GAP",
#       "severity": "INFO",
#       "message": "Gap is 0.30x ATR; edge intact"
#     },
#     ...
#   ],
#   "timestamp": "2026-05-01T14:30:00+00:00"
# }
```

---

## FAQ

**Q: Can I use custom stop / target logic?**  
A: Not yet. The engine is deterministic and rule-based by design. If you need different stop/target math, override `config` thresholds or extend the decision post-call (the engine provides the base decision; you can adjust if needed).

**Q: What if my broker fills at a different price than suggested?**  
A: The `suggested_entry` / `suggested_stop` are guidelines. Log the actual filled price and create a new `OpenPosition` with that price; the `manage_position()` call uses whatever current price you pass.

**Q: Can I run `manage_position()` more than every 15 minutes?**  
A: Yes. The function is idempotent given the same inputs. You can run it every minute or every hour — it will produce consistent decisions based on the latest price and candles you provide.

**Q: How do I handle gaps down overnight?**  
A: `manage_position()` will immediately hit the stop if latest_price ≤ stop_loss, returning EXIT with severity CRITICAL.

**Q: Can I override a SKIP decision?**  
A: The engine never forces you to follow its decision — it's a recommendation. If you have additional context, you can override it, but you lose the deterministic transparency advantage.

---

## See also

- **CLAUDE.md** — Project context and quirks
- **app/classification/mapper.py** — How RiskLayer probabilities → classification
- **app/models/inference/** — Model predictions and calibration
- **tests/** — Full test suite with many usage examples
