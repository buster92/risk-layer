"""
risklayer.execution.entry_evaluator
===================================

Decide WAIT / ENTER / SKIP for a RiskLayer candidate during the first
0–30 minutes after the open.

The evaluator never invents trades — it only confirms whether one of a
small set of well-defined patterns is present.  All thresholds are
sourced from ``risklayer.execution.config``.

Inputs (positional + keyword):
    candidate     : dict-like, RiskLayer output (must contain at least a
                    ``ticker`` key; uses p_continue_5d / p_drawdown_5d /
                    risk_score / setup_quality_score for confidence math).
    candles_1m    : list[Candle]   ── intraday 1-minute bars
    candles_5m    : list[Candle]   ── intraday 5-minute bars (optional)
    atr           : float          ── prior-session ATR (price units)
    open_price    : float          ── today's official open
    prev_close    : float          ── prior-session close
    latest_price  : float          ── current price (usually candles_1m[-1].close)

Returns ``ExecutionDecision``.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Mapping, Optional, Sequence

from risklayer.execution._candles import Candle, closes, sma, volumes
from risklayer.execution.config import ExecutionConfig, get_config
from risklayer.execution.decision_types import (
    DecisionReason,
    ExecutionAction,
    ExecutionDecision,
    Severity,
    TradeMode,
)


# ── Public API ─────────────────────────────────────────────────────────────


def evaluate_entry(
    candidate: Mapping[str, Any],
    *,
    candles_1m: Sequence[Candle],
    candles_5m: Optional[Sequence[Candle]] = None,
    atr: float,
    open_price: float,
    prev_close: float,
    latest_price: Optional[float] = None,
    market_open: Optional[dt.datetime] = None,
    config: Optional[ExecutionConfig] = None,
) -> ExecutionDecision:
    """Run the rule cascade and return one ``ExecutionDecision``.

    *market_open* defaults to the timestamp of the first 1m candle.  Pass
    it explicitly when running tests with synthetic candles.
    """
    cfg = get_config(config)
    ticker = str(candidate.get("ticker") or "?")
    decision = ExecutionDecision(ticker=ticker, action=ExecutionAction.WAIT)

    # ── Sanity-check inputs ────────────────────────────────────────────
    if atr is None or atr <= 0:
        decision.action = ExecutionAction.SKIP
        decision.add_reason("INVALID_ATR", Severity.CRITICAL, "ATR is missing or non-positive.")
        return decision
    if open_price is None or prev_close is None:
        decision.action = ExecutionAction.SKIP
        decision.add_reason("INVALID_PRICES", Severity.CRITICAL, "open_price or prev_close missing.")
        return decision
    if not candles_1m:
        decision.action = ExecutionAction.WAIT
        decision.add_reason(
            "NO_INTRADAY_DATA",
            Severity.WARNING,
            "No 1m candles available yet — cannot evaluate entry pattern.",
        )
        return decision

    if latest_price is None:
        latest_price = candles_1m[-1].close
    if market_open is None:
        market_open = candles_1m[0].timestamp

    # ── Rule A: open gap is too extended vs ATR ───────────────────────
    open_gap_atr = abs(open_price - prev_close) / atr
    if open_gap_atr >= cfg.max_open_gap_atr:
        decision.action = ExecutionAction.SKIP
        decision.add_reason(
            "GAP_TOO_LARGE",
            Severity.CRITICAL,
            f"Open gap is {open_gap_atr:.2f}x ATR (>= {cfg.max_open_gap_atr:.2f}); "
            "the model's edge is gone.",
        )
        return decision

    # ── Rule B: price has moved too far from open ─────────────────────
    extension_atr = abs(latest_price - open_price) / atr
    if extension_atr >= cfg.max_current_extension_atr:
        if latest_price >= open_price:
            decision.action = ExecutionAction.SKIP
            decision.add_reason(
                "EXTENDED_FROM_OPEN",
                Severity.CRITICAL,
                f"Price is {extension_atr:.2f}x ATR above open — chasing risk too high.",
            )
        else:
            decision.action = ExecutionAction.WAIT
            decision.add_reason(
                "FLUSH_FROM_OPEN",
                Severity.WARNING,
                f"Price is {extension_atr:.2f}x ATR below open — wait for stabilization.",
            )
        return decision

    # ── Rule C: open-noise window ─────────────────────────────────────
    elapsed_minutes = (candles_1m[-1].timestamp - market_open).total_seconds() / 60.0
    if elapsed_minutes < cfg.open_noise_minutes:
        decision.action = ExecutionAction.WAIT
        decision.add_reason(
            "OPEN_NOISE_WINDOW",
            Severity.INFO,
            f"Only {elapsed_minutes:.1f} min since open — within "
            f"{cfg.open_noise_minutes}-min noise window.",
        )
        return decision

    if elapsed_minutes > cfg.entry_window_minutes:
        # Outside the entry window: still produce a decision but flag it.
        decision.add_reason(
            "OUTSIDE_ENTRY_WINDOW",
            Severity.WARNING,
            f"{elapsed_minutes:.0f} min since open — past the "
            f"{cfg.entry_window_minutes}-min entry window.",
        )

    # ── Rule D: pattern detection in 5–30 minute window ───────────────
    return _evaluate_patterns(
        decision=decision,
        candidate=candidate,
        candles_1m=candles_1m,
        candles_5m=candles_5m,
        atr=atr,
        open_price=open_price,
        latest_price=latest_price,
        cfg=cfg,
    )


# ── Pattern dispatch ───────────────────────────────────────────────────────


def _evaluate_patterns(
    *,
    decision: ExecutionDecision,
    candidate: Mapping[str, Any],
    candles_1m: Sequence[Candle],
    candles_5m: Optional[Sequence[Candle]],
    atr: float,
    open_price: float,
    latest_price: float,
    cfg: ExecutionConfig,
) -> ExecutionDecision:
    """Run pattern checks in priority order.

    Order matters: invalid patterns short-circuit before valid ones, so a
    failed-breakout wick is never mis-classified as a reclaim.
    """
    # Compute the "reclaim level" (short MA cluster).  Use SMA9 if there
    # are at least 9 candles; otherwise fall back to the open price.
    closes_1m = closes(candles_1m)
    reclaim_level = sma(closes_1m, 9) or open_price

    # ── Invalid / wait patterns first ─────────────────────────────────
    if _is_failed_breakout(candles_1m, reclaim_level):
        decision.action = ExecutionAction.WAIT
        decision.add_reason(
            "FAILED_BREAKOUT",
            Severity.NEGATIVE,
            "Latest candle wick crossed reclaim but closed below — failed breakout.",
        )
        return decision

    if _is_rejection(candles_1m, cfg):
        decision.action = ExecutionAction.WAIT
        decision.add_reason(
            "UPPER_WICK_REJECTION",
            Severity.NEGATIVE,
            "Large upper wick on the latest 1m candle — rejection at resistance.",
        )
        return decision

    if _is_first_bounce_after_flush(candles_1m):
        decision.action = ExecutionAction.WAIT
        decision.add_reason(
            "FIRST_BOUNCE_NO_BASE",
            Severity.WARNING,
            "First green candle after a flush — no base / stabilization yet.",
        )
        return decision

    # ── Valid entry patterns ──────────────────────────────────────────
    extension_above_open = (latest_price - open_price) / atr

    # 5m confirmation pattern (highest confidence) — check first because
    # a 5m close above the level is a stronger signal than the 1m hold.
    if _is_5m_confirmation(candles_5m, reclaim_level):
        return _build_enter_decision(
            decision=decision,
            candidate=candidate,
            mode=TradeMode.CONFIRMED_RECLAIM,
            entry=latest_price,
            reclaim_level=reclaim_level,
            atr=atr,
            confidence=0.78,
            primary_reason=DecisionReason(
                "FIVE_MIN_CONFIRMATION",
                Severity.POSITIVE,
                "5m candle closed above reclaim level near the top of its range.",
            ),
            extension_above_open=extension_above_open,
            cfg=cfg,
        )

    if _is_pullback_and_reclaim(candles_1m, reclaim_level):
        return _build_enter_decision(
            decision=decision,
            candidate=candidate,
            mode=TradeMode.CONFIRMED_RECLAIM,
            entry=latest_price,
            reclaim_level=reclaim_level,
            atr=atr,
            confidence=0.70,
            primary_reason=DecisionReason(
                "PULLBACK_RECLAIM",
                Severity.POSITIVE,
                "Early flush stabilized and price reclaimed the prior lost level.",
            ),
            extension_above_open=extension_above_open,
            cfg=cfg,
        )

    if _is_reclaim_and_hold(candles_1m, reclaim_level, cfg, atr, open_price, latest_price):
        return _build_enter_decision(
            decision=decision,
            candidate=candidate,
            mode=TradeMode.CONFIRMED_RECLAIM,
            entry=latest_price,
            reclaim_level=reclaim_level,
            atr=atr,
            confidence=0.72,
            primary_reason=DecisionReason(
                "RECLAIM_HELD",
                Severity.POSITIVE,
                f"Price reclaimed MA cluster and held for "
                f"{cfg.reclaim_hold_candles} consecutive 1m candles.",
            ),
            extension_above_open=extension_above_open,
            cfg=cfg,
        )

    # No clean pattern — keep waiting.
    decision.action = ExecutionAction.WAIT
    decision.add_reason(
        "NO_PATTERN",
        Severity.INFO,
        "No clean reclaim or confirmation pattern yet.",
    )
    return decision


# ── Pattern primitives ─────────────────────────────────────────────────────


def _is_reclaim_and_hold(
    candles: Sequence[Candle],
    level: float,
    cfg: ExecutionConfig,
    atr: float,
    open_price: float,
    latest_price: float,
) -> bool:
    """Reclaim-and-hold: price crossed above ``level`` and the last
    ``reclaim_hold_candles`` closes are all above it, with no oversized
    upper-wick rejection on the last candle, and not extended past
    ``reclaim_max_extension_atr * atr`` above open."""
    n = cfg.reclaim_hold_candles
    if len(candles) < n + 1:
        return False

    recent = list(candles[-n:])
    if not all(c.close > level for c in recent):
        return False

    # Require a true *cross* — at least one earlier candle was below the
    # level (otherwise we never "reclaimed" anything).
    earlier = list(candles[:-n])
    if not any(c.close <= level for c in earlier):
        return False

    last = candles[-1]
    if last.upper_wick_ratio() > cfg.reclaim_max_upper_wick_ratio:
        return False

    if (latest_price - open_price) > cfg.reclaim_max_extension_atr * atr:
        return False

    return True


def _is_pullback_and_reclaim(candles: Sequence[Candle], level: float) -> bool:
    """Early red flush → declining sell volume → green reclaim that holds."""
    if len(candles) < 6:
        return False

    # A "flush" exists if any of the first 60% of bars closed below open
    # by more than 0.1% (small but non-zero).
    cutoff = max(2, int(len(candles) * 0.6))
    early = candles[:cutoff]
    flush_idx = -1
    for i, c in enumerate(early):
        if c.is_red and (c.open - c.close) / c.open > 0.001:
            flush_idx = i

    if flush_idx < 0:
        return False

    # Volume should taper: peak red volume occurs in the early window,
    # and the latest candle's volume is materially lower.
    early_vol = max(volumes(early)) if any(volumes(early)) else 0.0
    last_two = candles[-2:]
    if early_vol > 0 and last_two[-1].volume >= early_vol:
        # Volume is not contracting — not a calm reclaim.
        return False

    # Last two candles must close above the level, and the most recent
    # must be green (the actual reclaim).
    if not all(c.close > level for c in last_two):
        return False
    if not last_two[-1].is_green:
        return False

    return True


def _is_5m_confirmation(
    candles_5m: Optional[Sequence[Candle]],
    level: float,
) -> bool:
    """Latest 5m candle closes above ``level`` and near its high."""
    if not candles_5m:
        return False
    last = candles_5m[-1]
    if last.close <= level:
        return False
    if last.range <= 0:
        return False
    # "Near upper part of candle" — close in the top 35% of the range.
    upper_band = last.low + 0.65 * last.range
    return last.close >= upper_band


def _is_failed_breakout(candles: Sequence[Candle], level: float) -> bool:
    """Latest candle's high exceeded ``level`` but its close is below it."""
    if not candles:
        return False
    last = candles[-1]
    return last.high > level and last.close < level


def _is_rejection(candles: Sequence[Candle], cfg: ExecutionConfig) -> bool:
    """Latest candle has an oversized upper wick (rejection)."""
    if not candles:
        return False
    last = candles[-1]
    if last.range <= 0:
        return False
    # A rejection is an upper wick that dwarfs the body.
    return (
        last.upper_wick_ratio() > cfg.reclaim_max_upper_wick_ratio
        and last.upper_wick > 1.5 * last.body
    )


def _is_first_bounce_after_flush(candles: Sequence[Candle]) -> bool:
    """A single green candle right after a large red one — no base yet.

    "Large red" = body > median range of the prior window AND red.
    "No base"   = exactly one green candle since the flush.
    """
    if len(candles) < 4:
        return False

    last = candles[-1]
    if not last.is_green:
        return False

    # Find the most recent red candle.
    last_red_idx: Optional[int] = None
    for i in range(len(candles) - 2, -1, -1):
        if candles[i].is_red:
            last_red_idx = i
            break
    if last_red_idx is None:
        return False

    # Count green candles since (exclusive of) the last red.
    greens_since = sum(1 for c in candles[last_red_idx + 1 :] if c.is_green)
    if greens_since != 1:
        return False

    # The red candle should be visibly large — body > 60% of its range,
    # so wick-only candles don't count.
    red = candles[last_red_idx]
    if red.range <= 0:
        return False
    if red.body / red.range < 0.6:
        return False

    return True


# ── ENTER decision builder ────────────────────────────────────────────────


def _build_enter_decision(
    *,
    decision: ExecutionDecision,
    candidate: Mapping[str, Any],
    mode: TradeMode,
    entry: float,
    reclaim_level: float,
    atr: float,
    confidence: float,
    primary_reason: DecisionReason,
    extension_above_open: float,
    cfg: ExecutionConfig,
) -> ExecutionDecision:
    """Populate stop / TP / reasons for an ENTER decision.

    Stop placement
    --------------
    OPEN_ENTRY        : entry - 1 ATR (RiskLayer default).
    CONFIRMED_RECLAIM : structure stop = reclaim_level - 0.25 ATR
                        (just below the level we held).
    """
    decision.action = ExecutionAction.ENTER
    decision.mode = mode
    decision.confidence = confidence
    decision.suggested_entry = entry
    decision.reasons.insert(0, primary_reason)

    if mode == TradeMode.OPEN_ENTRY:
        stop = entry - atr
        tp = entry + 2 * atr
        decision.suggested_stop = stop
        decision.suggested_take_profit = tp
        decision.conservative_take_profit = tp
        decision.swing_take_profit = entry + 3 * atr
        decision.invalidation_level = stop
    else:
        # Structure-based stop: just under reclaim level.
        structure_stop = reclaim_level - 0.25 * atr
        # Never let the structure stop be tighter than 0.4 ATR — a hair-
        # trigger stop will get noise-stopped immediately.
        min_stop = entry - 0.4 * atr
        stop = min(structure_stop, min_stop)
        risk = entry - stop
        if risk <= 0:
            # Fallback: ATR stop if structure math went sideways.
            stop = entry - atr
            risk = atr
        conservative_tp = entry + 2 * risk  # 2R
        # Swing TP: prefer RiskLayer's own ATR target (entry + 3 ATR), but
        # only "valid" if p_continue_5d remains strong.
        p_cont_5d = float(candidate.get("p_continue_5d") or 0.0)
        swing_tp = entry + 3 * atr if p_cont_5d >= 0.55 else entry + 2.5 * atr

        decision.suggested_stop = stop
        decision.suggested_take_profit = conservative_tp
        decision.conservative_take_profit = conservative_tp
        decision.swing_take_profit = swing_tp
        decision.invalidation_level = reclaim_level

    # Add context reasons.
    if extension_above_open > 0.3:
        decision.add_reason(
            "MILDLY_EXTENDED",
            Severity.WARNING,
            f"Entry is {extension_above_open:.2f}x ATR above open — slightly extended.",
        )
    else:
        decision.add_reason(
            "NOT_EXTENDED",
            Severity.POSITIVE,
            "Entry is not extended vs. open.",
        )

    p_cont_5d = candidate.get("p_continue_5d")
    if p_cont_5d is not None and p_cont_5d < 0.55:
        decision.add_reason(
            "MODERATE_CONTINUATION",
            Severity.WARNING,
            f"p_continue_5d is only {p_cont_5d:.2f} — moderate model conviction.",
        )

    return decision
