from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class ExecutionAction(str, Enum):
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
    OPEN_ENTRY = "OPEN_ENTRY"
    CONFIRMED_RECLAIM = "CONFIRMED_RECLAIM"
    SWING_HOLD = "SWING_HOLD"


@dataclass
class DecisionReason:
    code: str
    severity: str
    message: str


@dataclass
class ExecutionDecision:
    ticker: str
    action: ExecutionAction
    mode: TradeMode
    confidence: float
    suggested_entry: Optional[float] = None
    suggested_stop: Optional[float] = None
    suggested_take_profit: Optional[float] = None
    conservative_take_profit: Optional[float] = None
    swing_take_profit: Optional[float] = None
    reduce_percent: Optional[float] = None
    rotate_to: Optional[str] = None
    reasons: list[DecisionReason] = field(default_factory=list)
    invalidation_level: Optional[float] = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return {
            **self.__dict__,
            "action": self.action.value,
            "mode": self.mode.value,
            "reasons": [r.__dict__ for r in self.reasons],
        }
