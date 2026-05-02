from dataclasses import dataclass


@dataclass(frozen=True)
class ExecutionConfig:
    open_noise_minutes: int = 5
    entry_window_minutes: int = 30
    max_open_gap_atr: float = 0.8
    max_current_extension_atr: float = 0.8
    reclaim_hold_candles: int = 2
    reduce_p_continue_drop: float = 0.08
    reduce_drawdown_rise: float = 0.10
    exit_p_continue_threshold: float = 0.50
    exit_drawdown_threshold: float = 0.40
    rotation_score_delta: float = 0.08
    strong_rotation_score_delta: float = 0.15


DEFAULT_CONFIG = ExecutionConfig()
