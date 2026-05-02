"""
risklayer.execution.config
==========================

Single source of truth for every threshold the execution engine uses.

If a number affects a trading decision, it lives here.  No magic numbers
inside the evaluator / manager / allocator modules.

The defaults below match the values agreed in the spec.  Overrides can be
applied per-call by constructing a custom ``ExecutionConfig`` and passing it
into the public functions, e.g.

    from risklayer.execution.config import ExecutionConfig
    cfg = ExecutionConfig(reduce_p_continue_drop=0.10)
    decision = evaluate_entry(candidate, ..., config=cfg)
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Optional


@dataclass(frozen=True)
class ExecutionConfig:
    """All tunable thresholds for the execution engine."""

    # ── Entry-window timing ─────────────────────────────────────────────────
    # First N minutes after the open are pure noise — never enter.
    open_noise_minutes: int = 5
    # Total window in which entry evaluation is meaningful.
    entry_window_minutes: int = 30

    # ── Extension limits (vs. ATR of the prior session) ────────────────────
    # If |open - prev_close| / ATR exceeds this, the open is too extended.
    max_open_gap_atr: float = 0.8
    # If |latest - open| / ATR exceeds this, price has run away from the
    # open and we no longer have an edge from the open-price entry.
    max_current_extension_atr: float = 0.8

    # ── Reclaim pattern requirements ───────────────────────────────────────
    # Number of consecutive 1m closes required to confirm a reclaim/hold.
    reclaim_hold_candles: int = 2
    # Reclaim is invalid if the next candle's upper wick exceeds this
    # fraction of the candle range (suggests rejection).
    reclaim_max_upper_wick_ratio: float = 0.55
    # An entry is "extended past open" if latest - open > this * ATR.
    reclaim_max_extension_atr: float = 0.5

    # ── Position-management — REDUCE thresholds ────────────────────────────
    # Drop in p_continue_5d (entry → now) that triggers a 30% reduce.
    reduce_p_continue_drop: float = 0.08
    # Rise in p_drawdown_5d (entry → now) that triggers a 30% reduce.
    reduce_drawdown_rise: float = 0.10
    # If both drop+rise happen, reduce 50% instead of 30%.
    reduce_combined_percent: float = 0.50
    reduce_single_percent: float = 0.30
    # Standalone "edge weakening" thresholds that trigger REDUCE / EXIT.
    weak_p_continue_threshold: float = 0.52
    weak_drawdown_threshold: float = 0.35

    # ── Position-management — EXIT thresholds ──────────────────────────────
    # If both conditions are met simultaneously, exit even without a price-
    # structure break (RiskLayer edge has collapsed).
    exit_p_continue_threshold: float = 0.50
    exit_drawdown_threshold: float = 0.40

    # ── Take-profit logic ──────────────────────────────────────────────────
    # Move stop to break-even (or slightly profitable) once price reaches
    # this multiple of initial risk.
    breakeven_r_multiple: float = 1.0
    # Optional partial take-profit at this multiple (30-50% of position).
    partial_tp_r_multiple: float = 1.5
    partial_tp_size: float = 0.40

    # ── Portfolio rotation ─────────────────────────────────────────────────
    # Score weights used by ``portfolio_allocator.candidate_score``.  Must
    # sum to 1.0; the defaults follow the spec.
    score_w_p_continue: float = 0.45
    score_w_drawdown: float = 0.30
    score_w_risk: float = 0.20
    score_w_setup_quality: float = 0.05

    # Score-delta thresholds.  ``new_score - current_score`` must beat one
    # of these to trigger any rotation at all.
    rotation_score_delta: float = 0.08
    strong_rotation_score_delta: float = 0.15

    # Rotation sizing (% of current position to rotate out of).
    rotation_partial_low: float = 0.30
    rotation_partial_high: float = 0.50
    rotation_strong_low: float = 0.50
    rotation_strong_high: float = 0.70
    rotation_minor_size: float = 0.20  # for "only slightly better" cases

    # ── Output / logging ───────────────────────────────────────────────────
    # When --json is passed, decisions are written here (one file per
    # ticker per day).  Path is interpreted relative to the project root.
    decisions_log_dir: str = "data/execution_decisions"

    def with_overrides(self, **overrides) -> "ExecutionConfig":
        """Return a copy with selected fields overridden."""
        return replace(self, **overrides)


# Module-level default — most callers should just import this.
DEFAULT_CONFIG: ExecutionConfig = ExecutionConfig()


def get_config(override: Optional[ExecutionConfig] = None) -> ExecutionConfig:
    """Return *override* if supplied, else the module default."""
    return override if override is not None else DEFAULT_CONFIG
