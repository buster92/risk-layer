def calculate_reward_pct(entry: float, target: float) -> float:
    return (target - entry) / entry


def calculate_risk_pct(entry: float, stop: float) -> float:
    return (entry - stop) / entry


def calculate_r_multiple(entry: float, stop: float, target: float) -> float:
    risk = entry - stop
    if risk <= 0:
        return 0.0
    return (target - entry) / risk


def calculate_simple_ev(win_probability: float, reward_pct: float, loss_probability: float, loss_pct: float) -> float:
    return (win_probability * reward_pct) - (loss_probability * loss_pct)
