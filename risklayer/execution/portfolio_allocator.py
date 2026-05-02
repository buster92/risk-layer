from __future__ import annotations

from .config import DEFAULT_CONFIG
from .decision_types import DecisionReason, ExecutionAction, ExecutionDecision, TradeMode


def candidate_score(metrics: dict) -> float:
    return (
        metrics["p_continue_5d"] * 0.45
        + (1 - metrics["p_drawdown_5d"]) * 0.30
        + (1 - metrics["risk_score"]) * 0.20
        + metrics.get("setup_quality", metrics.get("setup_quality_score", 0.0)) * 0.05
    )


def allocate_portfolio(current_position: dict, new_candidate: dict) -> ExecutionDecision:
    current = current_position["metrics"]
    newm = new_candidate["metrics"]
    cur_score = candidate_score(current)
    new_score = candidate_score(newm)
    delta = new_score - cur_score
    invalidated = current_position.get("invalidated", False)

    if invalidated and delta > 0:
        return ExecutionDecision(current_position["ticker"], ExecutionAction.ROTATE, TradeMode.SWING_HOLD, 0.9, reduce_percent=100, rotate_to=new_candidate["ticker"], reasons=[DecisionReason("CURRENT_INVALIDATED", "high", "Current position invalidated; rotate to new candidate")])
    if delta >= DEFAULT_CONFIG.strong_rotation_score_delta and current_position.get("structure_weak", False):
        return ExecutionDecision(current_position["ticker"], ExecutionAction.ROTATE, TradeMode.SWING_HOLD, 0.82, reduce_percent=60, rotate_to=new_candidate["ticker"], reasons=[DecisionReason("STRONGER_CANDIDATE", "medium", "New candidate significantly better and current weak")])
    if delta >= DEFAULT_CONFIG.rotation_score_delta:
        return ExecutionDecision(current_position["ticker"], ExecutionAction.ROTATE, TradeMode.SWING_HOLD, 0.74, reduce_percent=40, rotate_to=new_candidate["ticker"], reasons=[DecisionReason("BETTER_CANDIDATE", "medium", "Rotate partial capital to better candidate")])
    return ExecutionDecision(current_position["ticker"], ExecutionAction.HOLD, TradeMode.SWING_HOLD, 0.65, reasons=[DecisionReason("KEEP_CURRENT", "low", "New candidate only marginally better")])
