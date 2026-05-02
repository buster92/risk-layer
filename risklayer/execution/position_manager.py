"""
risklayer.execution.position_manager
====================================

Decide HOLD / REDUCE / EXIT / TAKE_PROFIT_* for an open position based on:

* the original entry parameters (entry, stop, target, mode, RiskLayer
  metrics captured at entry time)
* the latest RiskLayer metrics (if a new prediction has been produced)
* the latest 15-minute candles
* the latest tick price

Designed to be called every 15 minutes during the session.  The
``OpenPosition`` dataclass below is the only data contract callers need.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

from risklayer.execution._candles import Candle
from risklayer.execution.config import ExecutionConfig, get_config
from risklayer.execution.decision_types import (
    DecisionReason,
    ExecutionAction,
    ExecutionDecision,
    Severity,
    TradeMode,
)


# ── Data contract ──────────────────────────────────────────────────────────


@dataclass
class OpenPosition:
    """A snapshot of an open trade at the moment management runs.

    ``entry_metrics`` should be the RiskLayer metrics captured at entry
    time (so we can compare them to the latest values to detect edge
    decay).  All RiskLayer-related fields are optional — the engine
    degrades gracefully when they're missing.
    """

    ticker: str
    entry_price: float
    shares: float
    entry_time: dt.datetime
    stop_loss: float
    take_profit: float
    mode: TradeMode = TradeMode.OPEN_ENTRY
    invalidation_level: Optional[float] = None
    entry_metrics: Mapping[str, Any] = field(default_factory=dict)
    # Optional: the stop has been moved to break-even already this session.
    stop_at_breakeven: bool = False

    @property
    def initial_risk_per_share(self) -> float:
        """Distance from entry to original stop (always positive)."""
        return max(self.entry_price - self.stop_loss, 0.0)

    def r_multiple(self, price: float) -> float:
        """How many R the position is currently up (negative if losing)."""
        risk = self.initial_risk_per_share
        if risk <= 0:
            return 0.0
        return (price - self.entry_price) / risk


# ── Public API ─────────────────────────────────────────────────────────────


def manage_position(
    position: OpenPosition,
    *,
    latest_price: float,
    candles_15m: Optional[Sequence[Candle]] = None,
    latest_metrics: Optional[Mapping[str, Any]] = None,
    config: Optional[ExecutionConfig] = None,
) -> ExecutionDecision:
    """Return a structured decision for an open position.

    Action priority (highest → lowest):
      1. Stop hit                              → EXIT
      2. Take-profit hit                       → TAKE_PROFIT_FULL
      3. RiskLayer edge collapsed              → EXIT (subject to mode)
      4. 15m structure break below invalidation → EXIT (subject to mode)
      5. 1.5R reached                          → TAKE_PROFIT_PARTIAL
      6. Edge weakening                        → REDUCE
      7. 1R reached, stop not yet at breakeven → HOLD with breakeven note
      8. None of the above                     → HOLD
    """
    cfg = get_config(config)
    decision = ExecutionDecision(
        ticker=position.ticker,
        action=ExecutionAction.HOLD,
        mode=position.mode,
    )

    if latest_price is None or latest_price <= 0:
        decision.action = ExecutionAction.HOLD
        decision.add_reason("MISSING_PRICE", Severity.WARNING, "No latest price provided.")
        return decision

    risk = position.initial_risk_per_share
    r_mult = position.r_multiple(latest_price)
    decision.invalidation_level = position.invalidation_level or position.stop_loss

    # ── 1. Stop hit ───────────────────────────────────────────────────
    if latest_price <= position.stop_loss:
        decision.action = ExecutionAction.EXIT
        decision.confidence = 0.99
        decision.add_reason(
            "STOP_HIT",
            Severity.CRITICAL,
            f"Price {latest_price:.2f} touched stop loss {position.stop_loss:.2f}.",
        )
        return decision

    # ── 2. Take-profit hit ────────────────────────────────────────────
    if latest_price >= position.take_profit:
        decision.action = ExecutionAction.TAKE_PROFIT_FULL
        decision.confidence = 0.95
        decision.add_reason(
            "TAKE_PROFIT_HIT",
            Severity.POSITIVE,
            f"Price {latest_price:.2f} reached take-profit {position.take_profit:.2f}.",
        )
        return decision

    # ── 3 & 4. Structure / edge breakdowns ───────────────────────────
    structural_exit = _check_structure_break(position, candles_15m, cfg)
    edge_exit = _check_edge_collapse(position, latest_metrics, cfg)
    if structural_exit or edge_exit:
        # SWING_HOLD positions ignore minor 1m/15m noise — only exit on
        # an outright RiskLayer edge collapse.
        if position.mode == TradeMode.SWING_HOLD and structural_exit and not edge_exit:
            decision.add_reason(
                "SWING_IGNORE_NOISE",
                Severity.INFO,
                "Swing mode — ignoring intraday structure noise; "
                "will only exit on RiskLayer edge collapse or stop.",
            )
        else:
            decision.action = ExecutionAction.EXIT
            decision.confidence = 0.85
            for reason in (structural_exit, edge_exit):
                if reason:
                    decision.reasons.append(reason)
            return decision

    # ── 5. Partial take-profit at 1.5R ────────────────────────────────
    if risk > 0 and r_mult >= cfg.partial_tp_r_multiple:
        # Optional: only fire if momentum is weakening (latest 15m candle
        # closed red OR price is stalling against the take-profit).
        weakening = _is_momentum_weakening(candles_15m)
        if weakening:
            decision.action = ExecutionAction.TAKE_PROFIT_PARTIAL
            decision.reduce_percent = cfg.partial_tp_size
            decision.confidence = 0.75
            decision.add_reason(
                "PARTIAL_TP_AT_1_5R",
                Severity.POSITIVE,
                f"Price reached {r_mult:.2f}R and momentum weakening — "
                f"trim {cfg.partial_tp_size * 100:.0f}%.",
            )
            return decision

    # ── 6. Edge-weakening REDUCE ──────────────────────────────────────
    reduce = _check_reduce(position, latest_metrics, cfg)
    if reduce is not None:
        action, percent, reasons = reduce
        decision.action = action
        decision.reduce_percent = percent
        decision.confidence = 0.65
        decision.reasons.extend(reasons)
        return decision

    # ── 7. 1R reached → annotate breakeven move ───────────────────────
    if risk > 0 and r_mult >= cfg.breakeven_r_multiple and not position.stop_at_breakeven:
        decision.add_reason(
            "MOVE_STOP_TO_BREAKEVEN",
            Severity.POSITIVE,
            f"Price reached {r_mult:.2f}R — move stop to break-even "
            f"(or slightly profitable).",
        )
        # Attach the suggested new stop so callers can act on it.
        decision.suggested_stop = position.entry_price

    # ── 8. Default HOLD ───────────────────────────────────────────────
    if not decision.reasons:
        decision.add_reason(
            "THESIS_INTACT",
            Severity.INFO,
            "Price above invalidation, no edge collapse, no momentum failure.",
        )
    decision.action = ExecutionAction.HOLD
    return decision


# ── Internal rule helpers ──────────────────────────────────────────────────


def _check_structure_break(
    position: OpenPosition,
    candles_15m: Optional[Sequence[Candle]],
    cfg: ExecutionConfig,
) -> Optional[DecisionReason]:
    """15m close below the invalidation level OR a large red candle on
    elevated volume."""
    if not candles_15m:
        return None

    invalidation = position.invalidation_level
    last = candles_15m[-1]

    if invalidation is not None and last.close < invalidation:
        return DecisionReason(
            "INVALIDATION_BREAK",
            Severity.CRITICAL,
            f"15m candle closed at {last.close:.2f}, below invalidation "
            f"{invalidation:.2f}.",
        )

    # Large red on elevated volume relative to recent average.
    if len(candles_15m) >= 4 and last.is_red:
        recent = candles_15m[-4:-1]
        avg_range = sum(c.range for c in recent) / len(recent) if recent else 0.0
        avg_vol = sum(c.volume for c in recent) / len(recent) if recent else 0.0
        if avg_range > 0 and last.range > 1.5 * avg_range and last.volume > 1.3 * avg_vol:
            return DecisionReason(
                "LARGE_RED_VOLUME",
                Severity.NEGATIVE,
                "Outsized red 15m candle on elevated volume — structure breaking.",
            )

    return None


def _check_edge_collapse(
    position: OpenPosition,
    latest_metrics: Optional[Mapping[str, Any]],
    cfg: ExecutionConfig,
) -> Optional[DecisionReason]:
    """RiskLayer edge has fully collapsed."""
    if not latest_metrics:
        return None

    p_cont = _f(latest_metrics.get("p_continue_5d"))
    p_dd = _f(latest_metrics.get("p_drawdown_5d"))
    if p_cont is None or p_dd is None:
        return None

    if p_cont <= cfg.exit_p_continue_threshold and p_dd >= cfg.exit_drawdown_threshold:
        return DecisionReason(
            "EDGE_COLLAPSE",
            Severity.CRITICAL,
            f"RiskLayer edge collapsed: p_continue_5d={p_cont:.2f} "
            f"<= {cfg.exit_p_continue_threshold:.2f} and p_drawdown_5d={p_dd:.2f} "
            f">= {cfg.exit_drawdown_threshold:.2f}.",
        )
    return None


def _check_reduce(
    position: OpenPosition,
    latest_metrics: Optional[Mapping[str, Any]],
    cfg: ExecutionConfig,
) -> Optional[tuple[ExecutionAction, float, list[DecisionReason]]]:
    """Return (action, reduce_percent, reasons) if the edge is weakening,
    otherwise None."""
    if not latest_metrics:
        return None

    entry_p_cont = _f(position.entry_metrics.get("p_continue_5d"))
    entry_p_dd = _f(position.entry_metrics.get("p_drawdown_5d"))
    now_p_cont = _f(latest_metrics.get("p_continue_5d"))
    now_p_dd = _f(latest_metrics.get("p_drawdown_5d"))

    reasons: list[DecisionReason] = []
    drop = (
        entry_p_cont - now_p_cont
        if entry_p_cont is not None and now_p_cont is not None
        else 0.0
    )
    rise = (
        now_p_dd - entry_p_dd
        if entry_p_dd is not None and now_p_dd is not None
        else 0.0
    )

    drop_trigger = drop >= cfg.reduce_p_continue_drop
    rise_trigger = rise >= cfg.reduce_drawdown_rise

    if drop_trigger:
        reasons.append(DecisionReason(
            "P_CONTINUE_DROP",
            Severity.NEGATIVE,
            f"p_continue_5d dropped from {entry_p_cont:.2f} to {now_p_cont:.2f} "
            f"(Δ={drop:+.2f}).",
        ))
    if rise_trigger:
        reasons.append(DecisionReason(
            "DRAWDOWN_RISE",
            Severity.NEGATIVE,
            f"p_drawdown_5d rose from {entry_p_dd:.2f} to {now_p_dd:.2f} "
            f"(Δ={rise:+.2f}).",
        ))

    # Standalone weak edge (independent of entry deltas).
    weak_now = (
        now_p_cont is not None and now_p_cont < cfg.weak_p_continue_threshold
        and now_p_dd is not None and now_p_dd > cfg.weak_drawdown_threshold
    )
    if weak_now and not (drop_trigger or rise_trigger):
        reasons.append(DecisionReason(
            "WEAK_EDGE",
            Severity.WARNING,
            f"Current p_continue_5d={now_p_cont:.2f} < {cfg.weak_p_continue_threshold:.2f} "
            f"and p_drawdown_5d={now_p_dd:.2f} > {cfg.weak_drawdown_threshold:.2f}.",
        ))

    if not reasons:
        return None

    if drop_trigger and rise_trigger:
        return (ExecutionAction.REDUCE, cfg.reduce_combined_percent, reasons)

    return (ExecutionAction.REDUCE, cfg.reduce_single_percent, reasons)


def _is_momentum_weakening(candles_15m: Optional[Sequence[Candle]]) -> bool:
    """Heuristic: latest 15m candle red OR small body relative to recent."""
    if not candles_15m:
        return False
    last = candles_15m[-1]
    if last.is_red:
        return True
    if len(candles_15m) >= 4:
        recent = candles_15m[-4:-1]
        avg_body = sum(c.body for c in recent) / len(recent)
        if avg_body > 0 and last.body < 0.3 * avg_body:
            return True
    return False


def _f(value: Any) -> Optional[float]:
    """Safe float conversion that returns None on failure."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
