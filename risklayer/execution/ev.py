"""
risklayer.execution.ev
======================

Tiny set of pure helpers for risk / reward / expected-value math.

These are intentionally dumb single-purpose functions so that the rest of
the engine (and tests) can rely on them without any side effects.

All formulas assume a *long* trade (entry < target, entry > stop).  Short
support can be added later if needed; for now the project's universe is
US equity continuation longs.
"""
from __future__ import annotations

from typing import Optional


def calculate_risk_pct(entry: float, stop: float) -> float:
    """Loss as a fraction of entry if the stop is hit.

    Returns a non-negative number for valid long inputs.  Raises
    ``ValueError`` if the stop is at/above the entry (invalid for a long).
    """
    _validate_positive(entry, "entry")
    if stop >= entry:
        raise ValueError(f"stop ({stop}) must be below entry ({entry}) for a long trade")
    return (entry - stop) / entry


def calculate_reward_pct(entry: float, target: float) -> float:
    """Reward as a fraction of entry if the target is hit.

    Raises ``ValueError`` if the target is at/below the entry.
    """
    _validate_positive(entry, "entry")
    if target <= entry:
        raise ValueError(f"target ({target}) must be above entry ({entry}) for a long trade")
    return (target - entry) / entry


def calculate_r_multiple(entry: float, stop: float, target: float) -> float:
    """Reward-to-risk ratio in R units.

    R = (target - entry) / (entry - stop).
    A 2R trade returns 2 units of reward per 1 unit of risk.
    """
    risk = calculate_risk_pct(entry, stop)
    reward = calculate_reward_pct(entry, target)
    if risk == 0:
        # Should not happen given the validations above, but be defensive.
        return float("inf")
    return reward / risk


def calculate_simple_ev(
    win_probability: float,
    reward_pct: float,
    loss_probability: Optional[float] = None,
    loss_pct: Optional[float] = None,
) -> float:
    """Rough expected value as a fraction of position size.

    EV = p_win * reward + p_loss * (-loss)

    ``loss_probability`` defaults to ``1 - win_probability``.  ``loss_pct``
    must be provided as a positive number; the sign is handled internally.

    NOTE: ``win_probability`` here is illustrative only.  Using
    ``p_continue_5d`` directly as a true win probability over-states the
    model's claim — present this as a rough display, not as truth.
    """
    if not 0.0 <= win_probability <= 1.0:
        raise ValueError(f"win_probability {win_probability} not in [0, 1]")
    if reward_pct <= 0:
        raise ValueError(f"reward_pct ({reward_pct}) must be positive")
    if loss_pct is None or loss_pct <= 0:
        raise ValueError("loss_pct must be a positive fraction")

    if loss_probability is None:
        loss_probability = 1.0 - win_probability
    if not 0.0 <= loss_probability <= 1.0:
        raise ValueError(f"loss_probability {loss_probability} not in [0, 1]")

    return win_probability * reward_pct - loss_probability * loss_pct


# ── Internal ───────────────────────────────────────────────────────────────


def _validate_positive(value: float, name: str) -> None:
    if value is None or value <= 0:
        raise ValueError(f"{name} must be a positive price (got {value!r})")
