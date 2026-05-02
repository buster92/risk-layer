"""
risklayer.execution.decision_types
==================================

Structured types every execution decision flows through.

These are deliberately simple dataclasses with no business logic — the
modules that *produce* them (entry_evaluator / position_manager /
portfolio_allocator) own all the rules.  Keeping decisions plain makes them
trivial to serialize to JSON and to assert on in tests.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


# ── Enums ──────────────────────────────────────────────────────────────────


class ExecutionAction(str, Enum):
    """Every action the engine can recommend.

    Inheriting from ``str`` so the value serializes cleanly to JSON.
    """

    WAIT = "WAIT"
    ENTER = "ENTER"
    SKIP = "SKIP"
    HOLD = "HOLD"
    REDUCE = "REDUCE"
    EXIT = "EXIT"
    ROTATE = "ROTATE"
    TAKE_PROFIT_PARTIAL = "TAKE_PROFIT_PARTIAL"
    TAKE_PROFIT_FULL = "TAKE_PROFIT_FULL"


class TradeMode(str, Enum):
    """How the trade is being run — affects stop placement and tolerance."""

    OPEN_ENTRY = "OPEN_ENTRY"            # entered at the open, ATR-based stop
    CONFIRMED_RECLAIM = "CONFIRMED_RECLAIM"  # entered after reclaim, structure stop
    SWING_HOLD = "SWING_HOLD"            # multi-day, ignore intraday noise


class Severity(str, Enum):
    """Reason severity — used by reporting to colour / sort the output."""

    INFO = "INFO"
    POSITIVE = "POSITIVE"   # supports the action (e.g. "reclaim held")
    WARNING = "WARNING"     # cautionary but not decisive
    NEGATIVE = "NEGATIVE"   # works against the action
    CRITICAL = "CRITICAL"   # forces / dominates the action (e.g. stop hit)


# ── Reason / Decision dataclasses ──────────────────────────────────────────


@dataclass(frozen=True)
class DecisionReason:
    """One structured reason behind a decision.

    Fields
    ------
    code     : machine-readable identifier (e.g. "GAP_TOO_LARGE").  Stable
               across releases so downstream tooling can branch on it.
    severity : one of the ``Severity`` levels.
    message  : short human-readable explanation.
    """

    code: str
    severity: Severity
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "severity": self.severity.value, "message": self.message}


@dataclass
class ExecutionDecision:
    """The complete output of any execution call.

    All numeric "suggested_*" fields are optional because not every action
    needs them (a SKIP has no entry, a HOLD has no reduce_percent, etc.).
    """

    ticker: str
    action: ExecutionAction
    mode: Optional[TradeMode] = None
    confidence: float = 0.0  # 0.0-1.0, engine's own confidence in the call

    # Suggested trade parameters
    suggested_entry: Optional[float] = None
    suggested_stop: Optional[float] = None
    suggested_take_profit: Optional[float] = None
    conservative_take_profit: Optional[float] = None
    swing_take_profit: Optional[float] = None

    # Position-management extras
    reduce_percent: Optional[float] = None       # 0.0-1.0
    rotate_to: Optional[str] = None              # ticker symbol
    invalidation_level: Optional[float] = None   # price below which thesis dies

    reasons: list[DecisionReason] = field(default_factory=list)
    timestamp: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))

    # ── Convenience helpers ────────────────────────────────────────────
    def add_reason(self, code: str, severity: Severity, message: str) -> None:
        """Append a reason in-place (used internally by the evaluators)."""
        self.reasons.append(DecisionReason(code, severity, message))

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-friendly dict."""
        return {
            "ticker": self.ticker,
            "action": self.action.value,
            "mode": self.mode.value if self.mode else None,
            "confidence": round(float(self.confidence), 4),
            "suggested_entry": _round(self.suggested_entry),
            "suggested_stop": _round(self.suggested_stop),
            "suggested_take_profit": _round(self.suggested_take_profit),
            "conservative_take_profit": _round(self.conservative_take_profit),
            "swing_take_profit": _round(self.swing_take_profit),
            "reduce_percent": _round(self.reduce_percent, 4),
            "rotate_to": self.rotate_to,
            "invalidation_level": _round(self.invalidation_level),
            "reasons": [r.to_dict() for r in self.reasons],
            "timestamp": self.timestamp.isoformat(),
        }


def _round(value: Optional[float], digits: int = 4) -> Optional[float]:
    if value is None:
        return None
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None
