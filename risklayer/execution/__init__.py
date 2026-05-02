"""
risklayer.execution
===================

Rule-based execution engine that operates on top of RiskLayer model output.

Public surface:

    from risklayer.execution import (
        ExecutionAction,
        ExecutionDecision,
        TradeMode,
        DecisionReason,
        evaluate_entry,
        manage_position,
        evaluate_rotation,
    )

Important design principle
--------------------------
RiskLayer (the model) decides WHAT to trade.
This package decides WHEN and HOW to act.

All decisions are deterministic, fully driven by ``risklayer.execution.config``
thresholds, and return structured ``ExecutionDecision`` objects with
human-readable reasons.  No LLM is in the trading-decision path.
"""

from risklayer.execution.decision_types import (
    DecisionReason,
    ExecutionAction,
    ExecutionDecision,
    Severity,
    TradeMode,
)
from risklayer.execution.entry_evaluator import evaluate_entry
from risklayer.execution.portfolio_allocator import (
    PortfolioDecision,
    candidate_score,
    evaluate_rotation,
)
from risklayer.execution.position_manager import manage_position

__all__ = [
    "DecisionReason",
    "ExecutionAction",
    "ExecutionDecision",
    "PortfolioDecision",
    "Severity",
    "TradeMode",
    "candidate_score",
    "evaluate_entry",
    "evaluate_rotation",
    "manage_position",
]
