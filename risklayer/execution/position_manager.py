from __future__ import annotations

from .config import DEFAULT_CONFIG
from .decision_types import DecisionReason, ExecutionAction, ExecutionDecision, TradeMode


def manage_position(position: dict, latest_metrics: dict | None, candles_15m: list[dict], latest_price: float) -> ExecutionDecision:
    ticker = position["ticker"]
    mode = TradeMode(position.get("mode", TradeMode.CONFIRMED_RECLAIM))
    entry = position["entry_price"]
    stop = position["stop_loss"]
    tp = position["take_profit"]
    reasons: list[DecisionReason] = []

    if latest_price >= tp:
        return ExecutionDecision(ticker, ExecutionAction.TAKE_PROFIT_FULL, mode, 0.9, reasons=[DecisionReason("TP_HIT", "low", "Take profit reached")])
    if latest_price <= stop:
        return ExecutionDecision(ticker, ExecutionAction.EXIT, mode, 0.95, reasons=[DecisionReason("STOP_HIT", "high", "Stop loss breached")], invalidation_level=stop)

    r = entry - stop
    if r > 0 and latest_price >= entry + 1.5 * r:
        return ExecutionDecision(ticker, ExecutionAction.TAKE_PROFIT_PARTIAL, mode, 0.8, reduce_percent=40, reasons=[DecisionReason("R_1_5_REACHED", "medium", "Reached 1.5R, lock partial")])

    if r > 0 and latest_price >= entry + 1.0 * r:
        reasons.append(DecisionReason("MOVE_STOP_BREAKEVEN", "low", "At least 1R reached; move stop to breakeven"))

    if latest_metrics:
        p0 = position.get("p_continue_5d_entry", latest_metrics.get("p_continue_5d", 0.0))
        d0 = position.get("p_drawdown_5d_entry", latest_metrics.get("p_drawdown_5d", 0.0))
        p_now = latest_metrics.get("p_continue_5d", p0)
        d_now = latest_metrics.get("p_drawdown_5d", d0)

        if p_now <= DEFAULT_CONFIG.exit_p_continue_threshold and d_now >= DEFAULT_CONFIG.exit_drawdown_threshold:
            return ExecutionDecision(ticker, ExecutionAction.EXIT, mode, 0.9, reasons=[DecisionReason("EDGE_COLLAPSE", "high", "RiskLayer edge collapsed")], invalidation_level=stop)

        reduce_pct = 0
        if p0 - p_now >= DEFAULT_CONFIG.reduce_p_continue_drop:
            reduce_pct += 30
            reasons.append(DecisionReason("PCONT_DROP", "medium", "p_continue_5d dropped materially"))
        if d_now - d0 >= DEFAULT_CONFIG.reduce_drawdown_rise:
            reduce_pct += 30
            reasons.append(DecisionReason("DD_RISE", "medium", "p_drawdown_5d rose materially"))
        if reduce_pct > 0:
            return ExecutionDecision(ticker, ExecutionAction.REDUCE, mode, 0.75, reduce_percent=min(reduce_pct, 50), reasons=reasons)

    if mode != TradeMode.SWING_HOLD and candles_15m:
        last = candles_15m[-1]
        if last["close"] < position.get("invalidation_level", stop):
            return ExecutionDecision(ticker, ExecutionAction.EXIT, mode, 0.8, reasons=[DecisionReason("STRUCTURE_BREAK", "high", "15m close below invalidation")], invalidation_level=position.get("invalidation_level", stop))

    return ExecutionDecision(ticker, ExecutionAction.HOLD, mode, 0.65, reasons=reasons or [DecisionReason("THESIS_VALID", "low", "Hold while thesis remains valid")])
