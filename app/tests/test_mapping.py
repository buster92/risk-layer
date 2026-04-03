"""
app/tests/test_mapping.py

Tests for the deterministic classification mapper.
Verifies each rule branch fires correctly under controlled inputs.
"""
from __future__ import annotations

import pytest

from app.classification.mapper import PredictionBundle, classify, compute_flags
from app.core.constants import Classification, Flag


def make_bundle(**kwargs) -> PredictionBundle:
    defaults = dict(
        p_continue_3d=0.5,
        p_continue_5d=0.5,
        p_drawdown_5d=0.3,
        p_mean_revert_3d=0.3,
        rel_vol_20d=1.2,
        rvol_percentile=0.5,
        adx=20.0,
        adx_slope=0.0,
        di_spread=2.0,
        move_zscore=0.5,
        dist_mean_atr=0.5,
        dist_sma20=0.02,
        range_expansion_ratio=1.1,
        rvol_5d=0.2,
        ret_1d=0.01,
        gap_pct=0.005,
        sector_trend_state=1,
        market_regime=1,
        exhaustion_flag=0,
        consec_days=2,
        hh_hl_flag=0.8,
        alpha_spy_1d=0.005,
    )
    defaults.update(kwargs)
    return PredictionBundle(**defaults)


class TestFavorableSetup:
    def test_fires_correctly(self):
        bundle = make_bundle(
            p_continue_3d=0.50,
            p_drawdown_5d=0.25,
            adx=25.0,
            move_zscore=0.8,       # not overextended
            dist_sma20=0.03,
        )
        result = classify(bundle)
        assert result.classification == Classification.FAVORABLE_SETUP

    def test_does_not_fire_when_extended(self):
        bundle = make_bundle(
            p_continue_3d=0.50,
            p_drawdown_5d=0.25,
            adx=25.0,
            move_zscore=2.5,        # overextended
            dist_sma20=0.10,
        )
        result = classify(bundle)
        assert result.classification != Classification.FAVORABLE_SETUP


class TestBreakoutExhaustion:
    def test_exhaustion_flag_triggers(self):
        bundle = make_bundle(
            exhaustion_flag=1,
            p_continue_3d=0.40,
            rel_vol_20d=3.0,
            range_expansion_ratio=2.0,
        )
        result = classify(bundle)
        assert result.classification == Classification.BREAKOUT_EXHAUSTION

    def test_extension_plus_high_vol_triggers(self):
        bundle = make_bundle(
            exhaustion_flag=0,
            move_zscore=2.5,
            dist_sma20=0.12,
            rel_vol_20d=3.0,
            range_expansion_ratio=1.8,
            p_continue_3d=0.38,
        )
        result = classify(bundle)
        assert result.classification == Classification.BREAKOUT_EXHAUSTION


class TestSpeculativeSpike:
    def test_fires_with_weak_sector_and_high_vol(self):
        bundle = make_bundle(
            rel_vol_20d=2.5,
            sector_trend_state=-1,
            market_regime=-1,
            p_continue_3d=0.38,
            ret_1d=0.05,
            exhaustion_flag=0,
            move_zscore=1.0,
            dist_sma20=0.04,
        )
        result = classify(bundle)
        assert result.classification == Classification.SPECULATIVE_SPIKE


class TestWeakContinuation:
    def test_fires_with_low_cont_high_drawdown(self):
        bundle = make_bundle(
            p_continue_3d=0.32,
            p_drawdown_5d=0.55,
            rel_vol_20d=1.2,
            exhaustion_flag=0,
            move_zscore=0.5,
            dist_sma20=0.02,
            sector_trend_state=0,
            ret_1d=0.01,
        )
        result = classify(bundle)
        assert result.classification == Classification.WEAK_CONTINUATION


class TestPanicFlush:
    def test_fires_on_sharp_down_day(self):
        bundle = make_bundle(
            ret_1d=-0.06,
            rel_vol_20d=3.0,
            range_expansion_ratio=2.0,
            rvol_percentile=0.85,
            p_drawdown_5d=0.50,
            exhaustion_flag=0,
            move_zscore=-2.5,
            dist_sma20=-0.10,
        )
        result = classify(bundle)
        assert result.classification == Classification.PANIC_FLUSH


class TestFlags:
    def test_rel_vol_flag(self):
        bundle = make_bundle(rel_vol_20d=3.0)
        flags = compute_flags(bundle)
        assert Flag.REL_VOL_ELEVATED in flags

    def test_no_rel_vol_flag_when_normal(self):
        bundle = make_bundle(rel_vol_20d=1.1)
        flags = compute_flags(bundle)
        assert Flag.REL_VOL_ELEVATED not in flags

    def test_vol_expanding_flag(self):
        bundle = make_bundle(range_expansion_ratio=2.0)
        flags = compute_flags(bundle)
        assert Flag.VOL_EXPANDING in flags

    def test_gap_flag(self):
        bundle = make_bundle(gap_pct=0.04)
        flags = compute_flags(bundle)
        assert Flag.GAP_DRIVEN in flags

    def test_sector_aligned_flag(self):
        bundle = make_bundle(sector_trend_state=1, market_regime=1)
        flags = compute_flags(bundle)
        assert Flag.SECTOR_ALIGNED in flags

    def test_weak_sector_flag(self):
        bundle = make_bundle(sector_trend_state=-1, market_regime=-1)
        flags = compute_flags(bundle)
        assert Flag.WEAK_SECTOR in flags

    def test_adx_strengthening_flag(self):
        bundle = make_bundle(adx=30.0, adx_slope=0.05)
        flags = compute_flags(bundle)
        assert Flag.ADX_STRENGTHENING in flags

    def test_price_extended_flag(self):
        bundle = make_bundle(move_zscore=2.5, dist_sma20=0.10)
        flags = compute_flags(bundle)
        assert Flag.PRICE_EXTENDED in flags


class TestScores:
    def test_deception_score_bounded(self):
        for _ in range(20):
            import random
            bundle = make_bundle(
                p_drawdown_5d=random.random(),
                p_continue_3d=random.random(),
                rel_vol_20d=random.uniform(0.5, 4.0),
            )
            flags = compute_flags(bundle)
            from app.classification.mapper import compute_deception_score
            score = compute_deception_score(bundle, flags)
            assert 0.0 <= score <= 1.0

    def test_setup_quality_score_bounded(self):
        for _ in range(20):
            import random
            bundle = make_bundle(
                p_continue_3d=random.random(),
                p_continue_5d=random.random(),
                adx=random.uniform(10, 50),
                rel_vol_20d=random.uniform(0.5, 4.0),
            )
            flags = compute_flags(bundle)
            from app.classification.mapper import compute_setup_quality_score
            score = compute_setup_quality_score(bundle, flags)
            assert 0.0 <= score <= 1.0

    def test_missing_probabilities_returns_neutral(self):
        bundle = PredictionBundle()  # all None
        result = classify(bundle)
        assert result.classification == Classification.NEUTRAL
