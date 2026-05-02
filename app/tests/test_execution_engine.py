"""
app/tests/test_execution_engine.py
==================================

Unit tests for risklayer.execution.* modules.

Each scenario is built from synthetic candle lists so no DB / network /
yfinance dependency is required.  Tests cover the spec's "Definition of
done" checklist:

Entry evaluator
  - first 5 minutes returns WAIT
  - large gap returns SKIP
  - first bounce after flush returns WAIT
  - reclaim + 2 candle hold returns ENTER
  - failed resistance wick returns WAIT

Position manager
  - TP hit returns TAKE_PROFIT_FULL
  - 1R reached annotates breakeven move
  - p_continue drop triggers REDUCE
  - drawdown rise triggers REDUCE
  - stop hit returns EXIT
  - swing mode ignores minor intraday break

Portfolio allocator
  - better candidate triggers partial ROTATE
  - slightly better candidate returns KEEP
  - current invalidated + better candidate returns ROTATE with full reduce
"""
from __future__ import annotations

import datetime as dt
from typing import Sequence

import pytest

from risklayer.execution._candles import Candle
from risklayer.execution.config import DEFAULT_CONFIG, ExecutionConfig
from risklayer.execution.decision_types import (
    ExecutionAction,
    Severity,
    TradeMode,
)
from risklayer.execution.entry_evaluator import evaluate_entry
from risklayer.execution.ev import (
    calculate_r_multiple,
    calculate_reward_pct,
    calculate_risk_pct,
    calculate_simple_ev,
)
from risklayer.execution.portfolio_allocator import (
    candidate_score,
    evaluate_rotation,
)
from risklayer.execution.position_manager import OpenPosition, manage_position


# ── Helpers ────────────────────────────────────────────────────────────────

MARKET_OPEN = dt.datetime(2026, 5, 1, 9, 30, tzinfo=dt.timezone.utc)


def _candle(minute: int, o: float, h: float, l: float, c: float, v: float = 100_000) -> Candle:
    """Build a 1m candle at MARKET_OPEN + ``minute`` minutes."""
    return Candle(
        timestamp=MARKET_OPEN + dt.timedelta(minutes=minute),
        open=o,
        high=h,
        low=l,
        close=c,
        volume=v,
    )


def _sample_candidate(**overrides) -> dict:
    base = {
        "ticker": "TEST",
        "p_continue_3d": 0.55,
        "p_continue_5d": 0.58,
        "p_drawdown_5d": 0.27,
        "risk_score": 0.20,
        "setup_quality_score": 0.49,
    }
    base.update(overrides)
    return base


# ── Entry evaluator ────────────────────────────────────────────────────────


class TestEntryEvaluator:
    def test_first_minutes_returns_wait(self):
        """During the open-noise window the engine must wait."""
        candles = [_candle(i, 100.0, 100.5, 99.8, 100.2) for i in range(3)]
        decision = evaluate_entry(
            _sample_candidate(),
            candles_1m=candles,
            atr=2.0,
            open_price=100.0,
            prev_close=99.5,
            latest_price=100.2,
            market_open=MARKET_OPEN,
        )
        assert decision.action == ExecutionAction.WAIT
        codes = [r.code for r in decision.reasons]
        assert "OPEN_NOISE_WINDOW" in codes

    def test_large_gap_returns_skip(self):
        """An open gap >= max_open_gap_atr must SKIP."""
        # ATR = 2.0, gap = 3.0 → 1.5x ATR, way above the 0.8 threshold.
        candles = [_candle(i, 103.0, 103.5, 102.8, 103.2) for i in range(10)]
        decision = evaluate_entry(
            _sample_candidate(),
            candles_1m=candles,
            atr=2.0,
            open_price=103.0,
            prev_close=100.0,
            latest_price=103.2,
            market_open=MARKET_OPEN,
        )
        assert decision.action == ExecutionAction.SKIP
        assert any(r.code == "GAP_TOO_LARGE" for r in decision.reasons)

    def test_first_bounce_after_flush_returns_wait(self):
        """Single green candle after a large red one → WAIT, no base."""
        # 7 minutes of mild action, a big red candle, then exactly one
        # small green candle.
        candles: list[Candle] = []
        for i in range(7):
            candles.append(_candle(i, 100.0, 100.2, 99.9, 100.0))
        # Big red candle at minute 7 (range ~1.0, body ~0.9, body/range = 0.9).
        candles.append(_candle(7, 100.0, 100.05, 98.95, 99.0, v=500_000))
        # One small green candle — the "first bounce".
        candles.append(_candle(8, 99.0, 99.4, 99.0, 99.3, v=80_000))

        decision = evaluate_entry(
            _sample_candidate(),
            candles_1m=candles,
            atr=2.0,
            open_price=100.0,
            prev_close=99.8,
            latest_price=99.3,
            market_open=MARKET_OPEN,
        )
        assert decision.action == ExecutionAction.WAIT
        assert any(r.code == "FIRST_BOUNCE_NO_BASE" for r in decision.reasons)

    def test_reclaim_and_hold_returns_enter(self):
        """Price was below SMA9, crosses above, holds for 2 candles → ENTER."""
        # Build 9 candles trading just below 100.0 (SMA ≈ 99.95) and then
        # 3 candles closing well above the SMA.  The recent two must be
        # comfortably above the level with small upper wicks.
        candles: list[Candle] = []
        for i in range(9):
            candles.append(_candle(i, 99.9, 100.0, 99.8, 99.9))
        # Reclaim sequence: small bodies, closes 100.5+
        candles.append(_candle(9, 99.95, 100.55, 99.95, 100.5))
        candles.append(_candle(10, 100.5, 100.65, 100.45, 100.6))

        decision = evaluate_entry(
            _sample_candidate(),
            candles_1m=candles,
            atr=2.0,
            open_price=100.0,
            prev_close=99.5,
            latest_price=100.6,
            market_open=MARKET_OPEN,
        )
        assert decision.action == ExecutionAction.ENTER
        assert decision.mode == TradeMode.CONFIRMED_RECLAIM
        # Stop must be below entry, TP must be above entry.
        assert decision.suggested_stop is not None
        assert decision.suggested_stop < decision.suggested_entry
        assert decision.suggested_take_profit > decision.suggested_entry
        # Reason should include the reclaim code.
        assert any(r.code == "RECLAIM_HELD" for r in decision.reasons)

    def test_failed_resistance_wick_returns_wait(self):
        """A wick crosses resistance but the candle closes below it → WAIT."""
        candles: list[Candle] = []
        for i in range(9):
            candles.append(_candle(i, 100.0, 100.1, 99.9, 100.0))
        # Failed-breakout candle: wick to 100.6 but closes at 99.85
        # (below the SMA of ~100.0).
        candles.append(_candle(9, 100.0, 100.6, 99.8, 99.85))

        decision = evaluate_entry(
            _sample_candidate(),
            candles_1m=candles,
            atr=2.0,
            open_price=100.0,
            prev_close=99.5,
            latest_price=99.85,
            market_open=MARKET_OPEN,
        )
        assert decision.action == ExecutionAction.WAIT
        assert any(r.code == "FAILED_BREAKOUT" for r in decision.reasons)


# ── Position manager ───────────────────────────────────────────────────────


def _make_position(**overrides) -> OpenPosition:
    base = dict(
        ticker="XLY",
        entry_price=118.80,
        shares=82,
        entry_time=MARKET_OPEN,
        stop_loss=117.30,        # 1.50 below entry
        take_profit=121.80,      # 3.00 above entry → 2R
        mode=TradeMode.OPEN_ENTRY,
        invalidation_level=117.30,
        entry_metrics={"p_continue_5d": 0.64, "p_drawdown_5d": 0.24, "risk_score": 0.18},
    )
    base.update(overrides)
    return OpenPosition(**base)


class TestPositionManager:
    def test_take_profit_full_at_target(self):
        position = _make_position()
        decision = manage_position(position, latest_price=121.80)
        assert decision.action == ExecutionAction.TAKE_PROFIT_FULL
        assert any(r.code == "TAKE_PROFIT_HIT" for r in decision.reasons)

    def test_one_r_annotates_breakeven_move(self):
        position = _make_position()
        # 1R = 1.50 above entry → 120.30
        decision = manage_position(position, latest_price=120.30)
        assert decision.action == ExecutionAction.HOLD
        assert any(r.code == "MOVE_STOP_TO_BREAKEVEN" for r in decision.reasons)
        assert decision.suggested_stop == pytest.approx(position.entry_price)

    def test_p_continue_drop_triggers_reduce(self):
        position = _make_position()
        decision = manage_position(
            position,
            latest_price=119.50,
            latest_metrics={"p_continue_5d": 0.54, "p_drawdown_5d": 0.25},  # drop 0.10
        )
        assert decision.action == ExecutionAction.REDUCE
        assert decision.reduce_percent == DEFAULT_CONFIG.reduce_single_percent
        assert any(r.code == "P_CONTINUE_DROP" for r in decision.reasons)

    def test_drawdown_rise_triggers_reduce(self):
        position = _make_position()
        decision = manage_position(
            position,
            latest_price=119.50,
            latest_metrics={"p_continue_5d": 0.62, "p_drawdown_5d": 0.36},  # rise 0.12
        )
        assert decision.action == ExecutionAction.REDUCE
        assert any(r.code == "DRAWDOWN_RISE" for r in decision.reasons)

    def test_combined_drop_and_rise_uses_combined_percent(self):
        position = _make_position()
        decision = manage_position(
            position,
            latest_price=119.50,
            latest_metrics={"p_continue_5d": 0.54, "p_drawdown_5d": 0.36},
        )
        assert decision.action == ExecutionAction.REDUCE
        assert decision.reduce_percent == pytest.approx(DEFAULT_CONFIG.reduce_combined_percent)

    def test_stop_hit_returns_exit(self):
        position = _make_position()
        decision = manage_position(position, latest_price=117.20)  # below stop
        assert decision.action == ExecutionAction.EXIT
        assert any(r.code == "STOP_HIT" for r in decision.reasons)

    def test_swing_mode_ignores_minor_intraday_break(self):
        """Swing mode shouldn't EXIT on intraday structure noise alone."""
        position = _make_position(
            mode=TradeMode.SWING_HOLD,
            invalidation_level=118.50,  # current price will dip just below
        )
        # Build a 15m candle that closes just below invalidation.
        candle = Candle(
            timestamp=MARKET_OPEN + dt.timedelta(minutes=60),
            open=119.0,
            high=119.1,
            low=118.30,
            close=118.40,    # below 118.50 invalidation
            volume=10_000,
        )
        decision = manage_position(
            position,
            latest_price=118.45,  # still well above stop
            candles_15m=[candle],
            # No edge collapse in metrics.
            latest_metrics={"p_continue_5d": 0.60, "p_drawdown_5d": 0.25},
        )
        # Should NOT exit because swing mode tolerates intraday noise.
        assert decision.action == ExecutionAction.HOLD
        assert any(r.code == "SWING_IGNORE_NOISE" for r in decision.reasons)

    def test_edge_collapse_forces_exit(self):
        """Even a swing-mode position must exit when the RiskLayer edge
        fully collapses."""
        position = _make_position(mode=TradeMode.SWING_HOLD)
        decision = manage_position(
            position,
            latest_price=118.60,
            latest_metrics={"p_continue_5d": 0.45, "p_drawdown_5d": 0.45},
        )
        assert decision.action == ExecutionAction.EXIT
        assert any(r.code == "EDGE_COLLAPSE" for r in decision.reasons)


# ── Portfolio allocator ────────────────────────────────────────────────────


def _metrics(p_cont=0.55, p_dd=0.30, risk=0.30, setup=0.40) -> dict:
    return {
        "p_continue_5d": p_cont,
        "p_drawdown_5d": p_dd,
        "risk_score": risk,
        "setup_quality_score": setup,
    }


class TestPortfolioAllocator:
    def test_score_ordering_sanity(self):
        """A clearly stronger candidate should score higher."""
        weak = _metrics(p_cont=0.50, p_dd=0.35, risk=0.40, setup=0.30)
        strong = _metrics(p_cont=0.70, p_dd=0.20, risk=0.15, setup=0.65)
        assert candidate_score(strong) > candidate_score(weak)

    def test_better_candidate_triggers_partial_rotate(self):
        current = _metrics(p_cont=0.55, p_dd=0.30, risk=0.30, setup=0.40)
        new = _metrics(p_cont=0.68, p_dd=0.22, risk=0.20, setup=0.55)  # ~+0.10 delta
        result = evaluate_rotation(
            current_ticker="XLY",
            current_metrics=current,
            new_candidate_ticker="IWM",
            new_candidate_metrics=new,
        )
        assert result.score_delta >= DEFAULT_CONFIG.rotation_score_delta
        assert result.score_delta < DEFAULT_CONFIG.strong_rotation_score_delta
        assert result.decision.action == ExecutionAction.ROTATE
        assert result.decision.rotate_to == "IWM"
        assert 0.0 < result.decision.reduce_percent < 1.0

    def test_slightly_better_candidate_returns_keep(self):
        current = _metrics(p_cont=0.55, p_dd=0.30, risk=0.30, setup=0.40)
        new = _metrics(p_cont=0.57, p_dd=0.29, risk=0.29, setup=0.42)  # tiny edge
        result = evaluate_rotation(
            current_ticker="XLY",
            current_metrics=current,
            new_candidate_ticker="IWM",
            new_candidate_metrics=new,
        )
        assert result.score_delta < DEFAULT_CONFIG.rotation_score_delta
        assert result.decision.action == ExecutionAction.HOLD
        assert any(r.code == "DELTA_BELOW_THRESHOLD" for r in result.decision.reasons)

    def test_invalidated_position_full_rotate(self):
        current = _metrics(p_cont=0.55, p_dd=0.30, risk=0.30, setup=0.40)
        new = _metrics(p_cont=0.75, p_dd=0.18, risk=0.15, setup=0.65)  # very strong
        result = evaluate_rotation(
            current_ticker="XLY",
            current_metrics=current,
            new_candidate_ticker="IWM",
            new_candidate_metrics=new,
            current_position_invalidated=True,
        )
        assert result.decision.action == ExecutionAction.ROTATE
        assert result.decision.reduce_percent == pytest.approx(1.0)
        assert any(r.code == "CURRENT_INVALIDATED" for r in result.decision.reasons)

    def test_strong_better_uses_strong_size(self):
        current = _metrics(p_cont=0.50, p_dd=0.35, risk=0.40, setup=0.30)
        new = _metrics(p_cont=0.78, p_dd=0.18, risk=0.12, setup=0.70)
        result = evaluate_rotation(
            current_ticker="XLY",
            current_metrics=current,
            new_candidate_ticker="IWM",
            new_candidate_metrics=new,
        )
        assert result.score_delta >= DEFAULT_CONFIG.strong_rotation_score_delta
        assert result.decision.action == ExecutionAction.ROTATE
        # Strong rotate sits between rotation_strong_low and rotation_strong_high.
        assert (
            DEFAULT_CONFIG.rotation_strong_low
            <= result.decision.reduce_percent
            <= DEFAULT_CONFIG.rotation_strong_high
        )


# ── EV helpers ─────────────────────────────────────────────────────────────


class TestEV:
    def test_r_multiple(self):
        # entry=100, stop=99 (1 risk), target=102 (2 reward) → 2R
        assert calculate_r_multiple(100.0, 99.0, 102.0) == pytest.approx(2.0)

    def test_risk_and_reward_pct(self):
        assert calculate_risk_pct(100.0, 99.0) == pytest.approx(0.01)
        assert calculate_reward_pct(100.0, 102.0) == pytest.approx(0.02)

    def test_invalid_long_targets_raise(self):
        with pytest.raises(ValueError):
            calculate_reward_pct(100.0, 99.0)  # target below entry
        with pytest.raises(ValueError):
            calculate_risk_pct(100.0, 100.0)   # stop at entry

    def test_simple_ev_positive_when_p_win_high_enough(self):
        ev = calculate_simple_ev(
            win_probability=0.6,
            reward_pct=0.02,
            loss_pct=0.01,
        )
        # 0.6 * 0.02 - 0.4 * 0.01 = 0.012 - 0.004 = 0.008
        assert ev == pytest.approx(0.008)
