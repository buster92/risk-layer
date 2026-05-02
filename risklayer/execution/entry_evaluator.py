from __future__ import annotations

from .config import DEFAULT_CONFIG
from .decision_types import DecisionReason, ExecutionAction, ExecutionDecision, TradeMode


def _upper_wick_ratio(candle: dict) -> float:
    h, l, o, c = candle["high"], candle["low"], candle["open"], candle["close"]
    body_top = max(o, c)
    rng = max(h - l, 1e-9)
    return (h - body_top) / rng


def evaluate_entry(candidate: dict, candles_1m: list[dict], candles_5m: list[dict], atr: float, open_price: float, previous_close: float, latest_price: float, minutes_from_open: int) -> ExecutionDecision:
    ticker = candidate["ticker"]
    reasons = []
    open_gap_atr = abs(open_price - previous_close) / atr
    if open_gap_atr >= DEFAULT_CONFIG.max_open_gap_atr:
        return ExecutionDecision(ticker, ExecutionAction.SKIP, TradeMode.OPEN_ENTRY, 0.9, reasons=[DecisionReason("GAP_TOO_LARGE", "high", "Opening gap exceeded threshold")])

    ext = abs(latest_price - open_price) / atr
    if ext >= DEFAULT_CONFIG.max_current_extension_atr:
        action = ExecutionAction.WAIT if latest_price < open_price else ExecutionAction.SKIP
        return ExecutionDecision(ticker, action, TradeMode.OPEN_ENTRY, 0.8, reasons=[DecisionReason("TOO_EXTENDED", "high", "Price already moved too far from open")])

    if minutes_from_open < DEFAULT_CONFIG.open_noise_minutes:
        return ExecutionDecision(ticker, ExecutionAction.WAIT, TradeMode.OPEN_ENTRY, 0.6, reasons=[DecisionReason("OPEN_NOISE_WINDOW", "info", "First minutes after open are noisy")])

    if len(candles_1m) < 3:
        return ExecutionDecision(ticker, ExecutionAction.WAIT, TradeMode.OPEN_ENTRY, 0.5, reasons=[DecisionReason("INSUFFICIENT_DATA", "medium", "Need more 1m candles")])

    recent = candles_1m[-3:]
    reclaim = sum(c["close"] for c in candles_1m[-5:]) / min(5, len(candles_1m))
    hold_count = sum(1 for c in candles_1m[-DEFAULT_CONFIG.reclaim_hold_candles:] if c["close"] > reclaim)
    failed_breakout = recent[-1]["high"] > reclaim and recent[-1]["close"] < reclaim
    first_bounce_flush = recent[-2]["close"] < recent[-2]["open"] and recent[-1]["close"] > recent[-1]["open"] and recent[-2]["volume"] > recent[-1]["volume"] * 1.5

    if first_bounce_flush:
        return ExecutionDecision(ticker, ExecutionAction.WAIT, TradeMode.OPEN_ENTRY, 0.55, reasons=[DecisionReason("FIRST_BOUNCE_AFTER_FLUSH", "medium", "Initial bounce after flush lacks base")])

    if failed_breakout or _upper_wick_ratio(recent[-1]) > 0.5:
        return ExecutionDecision(ticker, ExecutionAction.WAIT, TradeMode.OPEN_ENTRY, 0.6, reasons=[DecisionReason("FAILED_BREAKOUT", "medium", "Breakout rejected by wick/close")])

    confirmed_5m = bool(candles_5m) and candles_5m[-1]["close"] > reclaim and (candles_5m[-1]["close"] - candles_5m[-1]["low"]) / max(candles_5m[-1]["high"] - candles_5m[-1]["low"], 1e-9) > 0.65
    if hold_count >= DEFAULT_CONFIG.reclaim_hold_candles and (latest_price - open_price) / atr <= 0.5:
        stop = min(c["low"] for c in candles_1m[-3:]) - 0.01
        risk = latest_price - stop
        conservative_tp = latest_price + 2 * risk
        swing_tp = candidate.get("take_profit") or conservative_tp
        reasons.extend([
            DecisionReason("RECLAIM_HELD", "low", "Reclaim held required candle count"),
            DecisionReason("NOT_EXTENDED", "low", "Price not over-extended versus ATR"),
        ])
        return ExecutionDecision(ticker, ExecutionAction.ENTER, TradeMode.CONFIRMED_RECLAIM, 0.8 if confirmed_5m else 0.72, suggested_entry=latest_price, suggested_stop=stop, suggested_take_profit=conservative_tp, conservative_take_profit=conservative_tp, swing_take_profit=swing_tp, reasons=reasons, invalidation_level=stop)

    return ExecutionDecision(ticker, ExecutionAction.WAIT, TradeMode.OPEN_ENTRY, 0.5, reasons=[DecisionReason("NO_VALID_PATTERN", "info", "No deterministic entry pattern confirmed")])
