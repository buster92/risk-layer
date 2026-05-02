from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from risklayer.execution.decision_types import ExecutionAction, TradeMode
from risklayer.execution.entry_evaluator import evaluate_entry
from risklayer.execution.portfolio_allocator import allocate_portfolio
from risklayer.execution.position_manager import manage_position


def _c(o, h, l, c, v=1000):
    return {"open": o, "high": h, "low": l, "close": c, "volume": v}


def test_entry_first_5_minutes_wait():
    d = evaluate_entry({"ticker": "AAA"}, [_c(10, 10.2, 9.9, 10.1)] * 6, [_c(10, 10.3, 9.8, 10.2)], 1.0, 10.0, 9.9, 10.1, 3)
    assert d.action == ExecutionAction.WAIT


def test_entry_large_gap_skip():
    d = evaluate_entry({"ticker": "AAA"}, [], [], 1.0, 11.0, 10.0, 11.0, 10)
    assert d.action == ExecutionAction.SKIP


def test_entry_first_bounce_wait():
    c1 = [_c(10, 10.1, 9.4, 9.5, 3000), _c(9.5, 9.8, 9.4, 9.7, 1000), _c(9.7, 9.8, 9.6, 9.7, 900)]
    d = evaluate_entry({"ticker": "AAA"}, c1, [_c(9.6, 9.9, 9.4, 9.7)], 1.0, 10.0, 10.0, 9.7, 8)
    assert d.action == ExecutionAction.WAIT


def test_entry_reclaim_enter():
    c = [_c(10, 10.1, 9.95, 10.0), _c(10.0, 10.2, 9.98, 10.1), _c(10.1, 10.25, 10.05, 10.2), _c(10.2, 10.3, 10.15, 10.22), _c(10.22, 10.35, 10.2, 10.3)]
    d = evaluate_entry({"ticker": "AAA", "take_profit": 11}, c, [_c(10.0, 10.4, 9.9, 10.35)], 1.0, 10.0, 10.0, 10.3, 10)
    assert d.action == ExecutionAction.ENTER


def test_entry_failed_wick_wait():
    c = [_c(10, 10.1, 9.9, 10.0), _c(10.0, 10.15, 9.95, 10.05), _c(10.05, 10.5, 10.0, 10.02)]
    d = evaluate_entry({"ticker": "AAA"}, c, [_c(10.0, 10.5, 9.9, 10.0)], 1.0, 10.0, 10.0, 10.02, 10)
    assert d.action == ExecutionAction.WAIT


def test_position_tp_full():
    p = {"ticker": "AAA", "entry_price": 10, "stop_loss": 9, "take_profit": 12, "mode": "CONFIRMED_RECLAIM"}
    d = manage_position(p, None, [], 12.1)
    assert d.action == ExecutionAction.TAKE_PROFIT_FULL


def test_position_1r_hold_with_reason():
    p = {"ticker": "AAA", "entry_price": 10, "stop_loss": 9, "take_profit": 13, "mode": "CONFIRMED_RECLAIM"}
    d = manage_position(p, None, [], 11.1)
    assert d.action == ExecutionAction.HOLD
    assert any(r.code == "MOVE_STOP_BREAKEVEN" for r in d.reasons)


def test_position_pcontinue_reduce():
    p = {"ticker": "AAA", "entry_price": 10, "stop_loss": 9, "take_profit": 12, "mode": "CONFIRMED_RECLAIM", "p_continue_5d_entry": 0.64, "p_drawdown_5d_entry": 0.2}
    d = manage_position(p, {"p_continue_5d": 0.54, "p_drawdown_5d": 0.2}, [], 10.5)
    assert d.action == ExecutionAction.REDUCE


def test_position_drawdown_reduce():
    p = {"ticker": "AAA", "entry_price": 10, "stop_loss": 9, "take_profit": 12, "mode": "CONFIRMED_RECLAIM", "p_continue_5d_entry": 0.64, "p_drawdown_5d_entry": 0.2}
    d = manage_position(p, {"p_continue_5d": 0.64, "p_drawdown_5d": 0.35}, [], 10.5)
    assert d.action == ExecutionAction.REDUCE


def test_position_stop_exit():
    p = {"ticker": "AAA", "entry_price": 10, "stop_loss": 9, "take_profit": 12, "mode": "CONFIRMED_RECLAIM"}
    d = manage_position(p, None, [], 8.9)
    assert d.action == ExecutionAction.EXIT


def test_swing_mode_ignores_minor_break():
    p = {"ticker": "AAA", "entry_price": 10, "stop_loss": 9, "take_profit": 12, "mode": "SWING_HOLD", "invalidation_level": 9.8}
    d = manage_position(p, None, [{"close": 9.7}], 9.9)
    assert d.action == ExecutionAction.HOLD
    assert d.mode == TradeMode.SWING_HOLD


def test_portfolio_better_candidate_rotate():
    cur = {"ticker": "CUR", "metrics": {"p_continue_5d": 0.55, "p_drawdown_5d": 0.35, "risk_score": 0.3, "setup_quality": 0.4}}
    new = {"ticker": "NEW", "metrics": {"p_continue_5d": 0.64, "p_drawdown_5d": 0.2, "risk_score": 0.2, "setup_quality": 0.5}}
    d = allocate_portfolio(cur, new)
    assert d.action == ExecutionAction.ROTATE


def test_portfolio_slightly_better_keep():
    cur = {"ticker": "CUR", "metrics": {"p_continue_5d": 0.60, "p_drawdown_5d": 0.25, "risk_score": 0.2, "setup_quality": 0.5}}
    new = {"ticker": "NEW", "metrics": {"p_continue_5d": 0.61, "p_drawdown_5d": 0.24, "risk_score": 0.2, "setup_quality": 0.5}}
    d = allocate_portfolio(cur, new)
    assert d.action == ExecutionAction.HOLD


def test_portfolio_invalidated_rotate_full():
    cur = {"ticker": "CUR", "invalidated": True, "metrics": {"p_continue_5d": 0.45, "p_drawdown_5d": 0.4, "risk_score": 0.4, "setup_quality": 0.3}}
    new = {"ticker": "NEW", "metrics": {"p_continue_5d": 0.62, "p_drawdown_5d": 0.22, "risk_score": 0.2, "setup_quality": 0.5}}
    d = allocate_portfolio(cur, new)
    assert d.action == ExecutionAction.ROTATE
    assert d.reduce_percent == 100
