"""
app/core/config.py
Central configuration loaded from environment variables.
"""
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ── App ────────────────────────────────────────────────────────────────────
    app_name: str = "MoveCred"
    app_version: str = "0.1.0"
    environment: str = "development"
    debug: bool = False

    # ── Database ───────────────────────────────────────────────────────────────
    database_url: str = "postgresql+psycopg2://movecred:movecred@localhost:5432/movecred"
    db_pool_size: int = 5
    db_max_overflow: int = 10

    # ── Market data provider ───────────────────────────────────────────────────
    # "yfinance" for prototype; swap to "polygon", "iex", "finnhub" in production
    market_data_provider: str = "yfinance"
    polygon_api_key: str = ""
    iex_api_key: str = ""
    finnhub_api_key: str = ""

    # ── Universe ───────────────────────────────────────────────────────────────
    active_universe_size: int = 50          # top N most active stocks per day
    active_universe_min_price: float = 5.0  # exclude penny stocks
    active_universe_min_volume: int = 500_000
    active_universe_min_rvol: float = 1.2   # min relative dollar-volume vs 20d avg (1.2 = 20% above avg)

    # ── Feature engineering ────────────────────────────────────────────────────
    feature_version: str = "v1"
    min_history_days: int = 252             # 1 year needed before features are valid
    sma_short: int = 20
    sma_mid: int = 50
    sma_long: int = 200
    atr_period: int = 14
    adx_period: int = 14
    vol_window_short: int = 5
    vol_window_long: int = 10
    vol_lookback_percentile: int = 252
    rel_vol_window: int = 20

    # ── Label thresholds ──────────────────────────────────────────────────────
    continuation_threshold_pct: float = 0.5    # % continuation required
    continuation_min_move_pct: float = 0.5     # ignore days with |ret_1d| below this
    drawdown_threshold_pct: float = 3.0        # % adverse move for drawdown label
    mean_revert_threshold_pct: float = 2.0     # % reversion for mean-revert label
    label_horizon_short: int = 3               # trading days
    label_horizon_long: int = 5                # trading days

    # ── Model ─────────────────────────────────────────────────────────────────
    model_version: str = "v1"
    model_artifacts_path: str = "app/models/artifacts"
    calibration_method: str = "isotonic"       # "isotonic" | "sigmoid"

    # ── Walk-forward validation ────────────────────────────────────────────────
    wf_train_years: int = 3
    wf_test_months: int = 3

    # ── Classification mapper ─────────────────────────────────────────────────
    strong_cont_threshold: float = 0.65
    weak_cont_threshold: float = 0.40
    high_drawdown_threshold: float = 0.45
    extension_zscore_threshold: float = 2.0
    high_relvol_threshold: float = 2.0

    # ── API ────────────────────────────────────────────────────────────────────
    api_prefix: str = "/v1"
    allow_origins: list[str] = ["*"]
    secret_key: str = "change-me-in-production"

    # ── Scheduler / jobs ──────────────────────────────────────────────────────
    jobs_timezone: str = "America/New_York"
    daily_ingest_hour: int = 17      # 5 PM ET — well after 4 PM close
    daily_ingest_minute: int = 0
    daily_predict_hour: int = 17
    daily_predict_minute: int = 30
    daily_digest_hour: int = 18
    daily_digest_minute: int = 0

    # ── Logging ───────────────────────────────────────────────────────────────
    log_level: str = "INFO"
    log_json: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
