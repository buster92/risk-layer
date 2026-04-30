"""
app/classification/mapper.py

Deterministic mapper from model probabilities + feature flags → classification label.

Rules are based on spec Section 4 / Layer 4.
The mapper is intentionally explicit and auditable — no black box.
Every classification branch maps directly to observable conditions.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.core.config import get_settings
from app.core.constants import Classification, ConfidenceBucket, Flag

settings = get_settings()


@dataclass
class PredictionBundle:
    """All inputs the mapper needs to produce a classification."""
    # Model probabilities
    p_continue_3d: float | None = None
    p_continue_5d: float | None = None
    p_drawdown_5d: float | None = None
    p_mean_revert_3d: float | None = None

    # Feature values (used for flag computation)
    rel_vol_20d: float | None = None
    rvol_percentile: float | None = None
    adx: float | None = None
    adx_slope: float | None = None
    di_spread: float | None = None
    move_zscore: float | None = None
    dist_mean_atr: float | None = None
    dist_sma20: float | None = None
    range_expansion_ratio: float | None = None
    rvol_5d: float | None = None
    ret_1d: float | None = None
    gap_pct: float | None = None
    sector_trend_state: int | None = None
    market_regime: int | None = None
    exhaustion_flag: int | None = None
    consec_days: int | None = None
    hh_hl_flag: float | None = None
    alpha_spy_1d: float | None = None


@dataclass
class ClassificationResult:
    classification: str
    interpretation: str
    flags: list[str]
    confidence_bucket: str
    risk_score: float
    deception_score: float
    setup_quality_score: float


def _safe(val: float | None, default: float = 0.0) -> float:
    return val if val is not None else default


def compute_flags(b: PredictionBundle) -> list[str]:
    """Return active explanation flags based on feature thresholds."""
    flags = []

    rel_vol = _safe(b.rel_vol_20d, 1.0)
    if rel_vol >= settings.high_relvol_threshold:
        flags.append(Flag.REL_VOL_ELEVATED)

    if _safe(b.range_expansion_ratio, 1.0) > 1.5 or _safe(b.rvol_percentile, 0.5) > 0.8:
        flags.append(Flag.VOL_EXPANDING)

    if abs(_safe(b.gap_pct, 0.0)) > 0.02:
        flags.append(Flag.GAP_DRIVEN)

    sector_state = b.sector_trend_state if b.sector_trend_state is not None else 0
    market = b.market_regime if b.market_regime is not None else 0
    if sector_state <= 0 and market <= 0:
        flags.append(Flag.WEAK_SECTOR)
    elif sector_state >= 1:
        flags.append(Flag.SECTOR_ALIGNED)

    if abs(_safe(b.dist_sma20, 0.0)) > 0.08 or _safe(b.move_zscore, 0.0) > settings.extension_zscore_threshold:
        flags.append(Flag.PRICE_EXTENDED)

    adx = _safe(b.adx, 20.0)
    adx_slope = _safe(b.adx_slope, 0.0)
    if adx > 25 and adx_slope > 0:
        flags.append(Flag.ADX_STRENGTHENING)
    elif adx > 25:
        flags.append(Flag.TREND_STRENGTH_IMPROVING)

    p3 = _safe(b.p_continue_3d, 0.5)
    if p3 < 0.42:
        flags.append(Flag.FOLLOW_THROUGH_WEAK)

    # Volume is "supportive" when it accompanies a move in an aligned sector context.
    # High volume into a weak sector is noise, not confirmation.
    if rel_vol >= 1.2 and sector_state >= 1:
        flags.append(Flag.REL_VOL_SUPPORTIVE)

    if _safe(b.p_mean_revert_3d, 0.0) > 0.55:
        flags.append(Flag.MEAN_REVERT_RISK)

    return flags


def compute_deception_score(b: PredictionBundle, flags: list[str]) -> float:
    """Weighted composite deception risk (0–1).

    Weights (sum = 1.0):
      0.30 × p_drawdown_5d         — adverse excursion risk (primary risk signal)
      0.25 × (1 − p_continue_3d)  — lack of follow-through conviction
      0.15 × p_mean_revert_3d     — mean reversion risk (move likely to fade)
      0.12 × PRICE_EXTENDED flag  — structural overextension
      0.10 × VOL_EXPANDING flag   — expanding volatility = instability
      0.08 × WEAK_SECTOR flag     — sector context not supporting the move
    """
    p_draw = _safe(b.p_drawdown_5d, 0.3)
    p_cont = _safe(b.p_continue_3d, 0.5)
    p_mean_rev = _safe(b.p_mean_revert_3d, 0.3)  # previously unused — now integrated
    ext = 1.0 if Flag.PRICE_EXTENDED in flags else 0.0
    vol_exp = 1.0 if Flag.VOL_EXPANDING in flags else 0.0
    weak_sec = 1.0 if Flag.WEAK_SECTOR in flags else 0.0

    score = (
        0.30 * p_draw
        + 0.25 * (1 - p_cont)
        + 0.15 * p_mean_rev
        + 0.12 * ext
        + 0.10 * vol_exp
        + 0.08 * weak_sec
    )
    return round(min(max(score, 0.0), 1.0), 4)


def compute_setup_quality_score(b: PredictionBundle, flags: list[str]) -> float:
    """Weighted composite setup quality (0–1). Higher = cleaner, lower-risk setup."""
    p3 = _safe(b.p_continue_3d, 0.5)
    p5 = _safe(b.p_continue_5d, 0.5)
    adx_strength = min(_safe(b.adx, 20.0) / 50.0, 1.0)
    rel_vol_norm = min(_safe(b.rel_vol_20d, 1.0) / 3.0, 1.0)
    sec_conf = 1.0 if Flag.SECTOR_ALIGNED in flags else 0.0
    over_ext = -0.15 if Flag.PRICE_EXTENDED in flags else 0.0
    unstable_vol = -0.10 if Flag.VOL_EXPANDING in flags else 0.0

    score = (
        0.35 * p3
        + 0.25 * p5
        + 0.15 * adx_strength
        + 0.15 * rel_vol_norm
        + 0.10 * sec_conf
        + over_ext
        + unstable_vol
    )
    return round(min(max(score, 0.0), 1.0), 4)


def classify(b: PredictionBundle) -> ClassificationResult:
    """
    Apply deterministic rule tree to assign a classification label.
    Rules execute top-to-bottom; first match wins.
    """
    flags = compute_flags(b)
    deception_score = compute_deception_score(b, flags)
    setup_quality_score = compute_setup_quality_score(b, flags)
    risk_score = deception_score

    p3 = _safe(b.p_continue_3d, 0.5)
    p5 = _safe(b.p_continue_5d, 0.5)
    p_draw = _safe(b.p_drawdown_5d, 0.3)
    adx = _safe(b.adx, 20.0)
    adx_slope = _safe(b.adx_slope, 0.0)
    exhaustion = bool(b.exhaustion_flag)
    ext = Flag.PRICE_EXTENDED in flags
    weak_sec = Flag.WEAK_SECTOR in flags
    sector_aligned = Flag.SECTOR_ALIGNED in flags
    rel_vol_high = Flag.REL_VOL_ELEVATED in flags
    vol_expanding = Flag.VOL_EXPANDING in flags
    adx_strengthening = Flag.ADX_STRENGTHENING in flags

    # ── Check if probabilities are reliable enough ─────────────────────────────
    models_missing = b.p_continue_3d is None and b.p_continue_5d is None
    if models_missing:
        return ClassificationResult(
            classification=Classification.NEUTRAL,
            interpretation="Insufficient model data for this stock.",
            flags=flags,
            confidence_bucket=ConfidenceBucket.INSUFFICIENT,
            risk_score=0.5,
            deception_score=0.5,
            setup_quality_score=0.5,
        )

    # ── Rule 1a: Strong continuation profile ──────────────────────────────────
    # Highest-confidence tier: both time horizons confirm, ADX is trending and
    # strengthening, sector is aligned, drawdown risk is low.
    if (
        p3 >= settings.strong_cont_threshold  # default 0.65 — both models agree
        and p5 >= 0.55                         # 5-day horizon confirms
        and adx > 25
        and adx_strengthening
        and sector_aligned
        and not ext
        and p_draw < 0.30
    ):
        cls = Classification.STRONG_CONTINUATION
        interp = (
            "High-conviction continuation setup: both near- and medium-term models "
            "agree, trend structure is strengthening, sector context is aligned, "
            "and adverse excursion risk is low."
        )
        bucket = ConfidenceBucket.HIGH

    # ── Rule 1b: Trend-confirming participation ────────────────────────────────
    elif (
        p3 >= 0.42
        and adx > 20
        and not weak_sec
        and not ext
        and p_draw < 0.38
    ):
        cls = Classification.FAVORABLE_SETUP
        interp = (
            "Structure is relatively clean: trend context supports the move, "
            "sector is not opposing, and adverse excursion risk is below average."
        )
        bucket = ConfidenceBucket.HIGH

    # ── Rule 2: Panic flush ────────────────────────────────────────────────────
    elif (
        _safe(b.ret_1d, 0.0) < -0.04
        and vol_expanding
        and rel_vol_high
        and p_draw >= 0.40
    ):
        cls = Classification.PANIC_FLUSH
        interp = (
            "A sharp decline with elevated volume often creates instability. "
            "Structure is fragile — risk of further dislocation is elevated."
        )
        bucket = ConfidenceBucket.MEDIUM

    # ── Rule 3: Breakout with exhaustion risk ─────────────────────────────────
    elif exhaustion or (ext and rel_vol_high and vol_expanding and p3 < 0.50):
        cls = Classification.BREAKOUT_EXHAUSTION
        interp = (
            "This move is attracting heavy attention, but similar setups usually "
            "fail to build stable follow-through."
        )
        bucket = ConfidenceBucket.MEDIUM

    # ── Rule 4: Speculative spike ──────────────────────────────────────────────
    elif (
        rel_vol_high
        and weak_sec
        and p3 < 0.45
        and abs(_safe(b.ret_1d, 0.0)) > 0.03
    ):
        cls = Classification.SPECULATIVE_SPIKE
        interp = (
            "This resembles a retail-style spike more than durable trend confirmation. "
            "Sector context does not support the move."
        )
        bucket = ConfidenceBucket.MEDIUM

    # ── Rule 5: High attention, low trust ─────────────────────────────────────
    elif (
        rel_vol_high
        and (p3 < settings.weak_cont_threshold or p_draw >= settings.high_drawdown_threshold)
    ):
        cls = Classification.HIGH_ATTENTION_LOW_TRUST
        interp = (
            "High volume is attracting attention, but similar setups usually fail "
            "to sustain momentum."
        )
        bucket = ConfidenceBucket.MEDIUM

    # ── Rule 6: Weak continuation ─────────────────────────────────────────────
    elif p3 <= settings.weak_cont_threshold and p_draw >= settings.high_drawdown_threshold:
        cls = Classification.WEAK_CONTINUATION
        interp = (
            "The setup shows unfavorable continuation odds. "
            "Adverse move probability is elevated relative to normal."
        )
        bucket = ConfidenceBucket.LOW

    # ── Default: neutral ──────────────────────────────────────────────────────
    else:
        cls = Classification.NEUTRAL
        interp = (
            "No dominant pattern detected. Continuation odds are near baseline. "
            "Monitor for clearer structure before acting."
        )
        bucket = ConfidenceBucket.LOW

    return ClassificationResult(
        classification=cls,
        interpretation=interp,
        flags=flags,
        confidence_bucket=bucket,
        risk_score=round(risk_score, 4),
        deception_score=round(deception_score, 4),
        setup_quality_score=round(setup_quality_score, 4),
    )
