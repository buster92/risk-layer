from app.core.config import get_settings


def test_wf_embargo_matches_label_horizon_long_minimum():
    """Embargo should be at least the longest forward label horizon."""
    settings = get_settings()
    assert settings.wf_embargo_days >= settings.label_horizon_long
    # Project default aligns exactly with 5-day continuation horizon.
    assert settings.wf_embargo_days == 5
