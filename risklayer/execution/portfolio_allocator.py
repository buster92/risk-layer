"""
risklayer.execution.portfolio_allocator
=======================================

Compare an open position to a freshly-ranked RiskLayer candidate and
decide whether to KEEP, partially ROTATE, or fully EXIT-AND-ROTATE.

Scoring formula (matches the spec):

    candidate_score =
          0.45 * p_continue_5d
        + 0.30 * (1 - p_drawdown_5d)
        + 0.20 * (1 - risk_score)
        + 0.05 * setup_quality_score

A higher score is better.  Rotation is gated by score deltas (see
``ExecutionConfig.rotation_score_delta`` / ``strong_rotation_score_delta``)
and by whether the current position has invalidated.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional

from risklayer.execution.config import ExecutionConfig, get_config
from risklayer.execution.decision_types import (
    DecisionReason,
    ExecutionAction,
    ExecutionDecision,
    Severity,
)


# ── Scoring ────────────────────────────────────────────────────────────────


def candidate_score(
    metrics: Mapping[str, Any],
    config: Optional[ExecutionConfig] = None,
) -> float:
    """Weighted RiskLayer score for ranking candidates against each other.

    Missing fields are treated as worst-case (0 for "good" features, 1 for
    "bad" features) so a candidate with incomplete data is never preferred
    to one with full metrics.
    """
    cfg = get_config(config)
    p_cont = _f(metrics.get("p_continue_5d"), default=0.0)
    p_dd = _f(metrics.get("p_drawdown_5d"), default=1.0)
    risk = _f(metrics.get("risk_score"), default=1.0)
    setup = _f(
        metrics.get("setup_quality_score") or metrics.get("setup_quality"),
        default=0.0,
    )

    return (
        cfg.score_w_p_continue * p_cont
        + cfg.score_w_drawdown * (1.0 - p_dd)
        + cfg.score_w_risk * (1.0 - risk)
        + cfg.score_w_setup_quality * setup
    )


# ── Public decision type ──────────────────────────────────────────────────


@dataclass
class PortfolioDecision:
    """Wrapper around an ``ExecutionDecision`` with the two scores attached."""

    decision: ExecutionDecision
    current_score: float
    new_score: float

    @property
    def score_delta(self) -> float:
        return self.new_score - self.current_score

    def to_dict(self) -> dict[str, Any]:
        return {
            "current_score": round(self.current_score, 4),
            "new_score": round(self.new_score, 4),
            "score_delta": round(self.score_delta, 4),
            "decision": self.decision.to_dict(),
        }


# ── Public API ─────────────────────────────────────────────────────────────


def evaluate_rotation(
    *,
    current_ticker: str,
    current_metrics: Mapping[str, Any],
    new_candidate_ticker: str,
    new_candidate_metrics: Mapping[str, Any],
    current_position_invalidated: bool = False,
    available_capital: Optional[float] = None,
    config: Optional[ExecutionConfig] = None,
) -> PortfolioDecision:
    """Compare two candidates and emit a rotation decision.

    Parameters
    ----------
    current_ticker / current_metrics
        The currently-held position and its *latest* RiskLayer metrics.
    new_candidate_ticker / new_candidate_metrics
        The new top candidate produced by RiskLayer today.
    current_position_invalidated
        True when ``position_manager`` has already flagged the current
        position as broken (price below invalidation, edge collapsed,
        etc.).  Only when this is True can a 100%-equivalent rotation
        happen.
    available_capital
        Carried through but not used in the rule logic — included for
        future sizing extensions.
    config
        Optional ``ExecutionConfig`` override.
    """
    cfg = get_config(config)

    current_score = candidate_score(current_metrics, cfg)
    new_score = candidate_score(new_candidate_metrics, cfg)
    delta = new_score - current_score

    # Same ticker: nothing to rotate.
    if new_candidate_ticker == current_ticker:
        decision = ExecutionDecision(
            ticker=current_ticker,
            action=ExecutionAction.HOLD,
            confidence=0.9,
        )
        decision.add_reason(
            "SAME_TICKER",
            Severity.INFO,
            "New candidate is the currently-held ticker — no rotation needed.",
        )
        return PortfolioDecision(decision, current_score, new_score)

    decision = ExecutionDecision(
        ticker=current_ticker,
        action=ExecutionAction.HOLD,
        rotate_to=new_candidate_ticker,
    )

    # Always include the headline score comparison.
    decision.add_reason(
        "SCORE_COMPARISON",
        Severity.INFO,
        f"Current {current_ticker} score={current_score:.3f} vs "
        f"new {new_candidate_ticker} score={new_score:.3f} (Δ={delta:+.3f}).",
    )

    # ── Decide ─────────────────────────────────────────────────────────
    if delta < cfg.rotation_score_delta:
        # Difference too small to justify rotation costs — KEEP.
        decision.action = ExecutionAction.HOLD
        decision.confidence = 0.7
        decision.add_reason(
            "DELTA_BELOW_THRESHOLD",
            Severity.INFO,
            f"Score delta {delta:+.3f} below rotation threshold "
            f"{cfg.rotation_score_delta:+.3f} — keep current position.",
        )
        decision.rotate_to = None
        return PortfolioDecision(decision, current_score, new_score)

    # New candidate is meaningfully better.  Choose sizing.
    if current_position_invalidated:
        # Full exit + rotate fully into the new name.
        decision.action = ExecutionAction.ROTATE
        decision.reduce_percent = 1.0  # exit current entirely
        decision.confidence = 0.9
        decision.add_reason(
            "CURRENT_INVALIDATED",
            Severity.CRITICAL,
            "Current position has invalidated — full exit and rotate to "
            f"{new_candidate_ticker}.",
        )
        return PortfolioDecision(decision, current_score, new_score)

    if delta >= cfg.strong_rotation_score_delta:
        # Strong difference but current still valid — rotate 50–70%.
        size = (cfg.rotation_strong_low + cfg.rotation_strong_high) / 2
        decision.action = ExecutionAction.ROTATE
        decision.reduce_percent = size
        decision.confidence = 0.8
        decision.add_reason(
            "STRONG_BETTER",
            Severity.POSITIVE,
            f"New candidate is meaningfully stronger (Δ={delta:+.3f}). "
            f"Rotate {size * 100:.0f}% into {new_candidate_ticker}.",
        )
        return PortfolioDecision(decision, current_score, new_score)

    # Modest improvement, current still valid — partial rotate 30–50%.
    size = (cfg.rotation_partial_low + cfg.rotation_partial_high) / 2
    # If the delta is just barely above threshold, lean to the smaller
    # "minor" rotation size (20–30%) per the spec.
    if delta < cfg.rotation_score_delta + 0.02:
        size = cfg.rotation_minor_size
        code = "MINOR_BETTER"
        message = (
            f"New candidate only marginally better (Δ={delta:+.3f}). "
            f"Reduce {size * 100:.0f}% only."
        )
    else:
        code = "MODEST_BETTER"
        message = (
            f"New candidate is moderately better (Δ={delta:+.3f}). "
            f"Rotate {size * 100:.0f}% into {new_candidate_ticker}."
        )

    decision.action = ExecutionAction.ROTATE
    decision.reduce_percent = size
    decision.confidence = 0.72
    decision.add_reason(code, Severity.POSITIVE, message)
    return PortfolioDecision(decision, current_score, new_score)


# ── Internal ───────────────────────────────────────────────────────────────


def _f(value: Any, *, default: float) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
