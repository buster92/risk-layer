"""
app/db/models.py
SQLAlchemy ORM models matching the spec schema exactly.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class JSONB(TypeDecorator):
    """Backend-agnostic JSONB.

    Uses PostgreSQL's native JSONB type (binary, indexable) when connected to
    Postgres.  Falls back to standard JSON for SQLite and other dialects so
    that unit tests can run without a real database.
    """

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            from sqlalchemy.dialects.postgresql import JSONB as PG_JSONB
            return dialect.type_descriptor(PG_JSONB())
        return dialect.type_descriptor(JSON())


class Base(DeclarativeBase):
    pass


# ─────────────────────────────────────────────────────────────────────────────
# stocks
# ─────────────────────────────────────────────────────────────────────────────
class Stock(Base):
    __tablename__ = "stocks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(16), nullable=False, unique=True, index=True)
    name: Mapped[str | None] = mapped_column(String(256))
    sector: Mapped[str | None] = mapped_column(String(128))
    industry: Mapped[str | None] = mapped_column(String(256))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    prices: Mapped[list["DailyPrice"]] = relationship(back_populates="stock")
    features: Mapped[list["DailyFeature"]] = relationship(back_populates="stock")
    predictions: Mapped[list["DailyPrediction"]] = relationship(back_populates="stock")
    universe_entries: Mapped[list["DailyActiveUniverse"]] = relationship(back_populates="stock")


# ─────────────────────────────────────────────────────────────────────────────
# daily_prices
# ─────────────────────────────────────────────────────────────────────────────
class DailyPrice(Base):
    __tablename__ = "daily_prices"
    __table_args__ = (UniqueConstraint("stock_id", "date", name="uq_price_stock_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"), nullable=False, index=True)
    date: Mapped[dt.date] = mapped_column(Date, nullable=False, index=True)
    open: Mapped[float | None] = mapped_column(Float)
    high: Mapped[float | None] = mapped_column(Float)
    low: Mapped[float | None] = mapped_column(Float)
    close: Mapped[float | None] = mapped_column(Float)
    adj_close: Mapped[float | None] = mapped_column(Float)
    volume: Mapped[int | None] = mapped_column(BigInteger)
    dollar_volume: Mapped[float | None] = mapped_column(Float)
    split_factor: Mapped[float | None] = mapped_column(Float)
    dividend: Mapped[float | None] = mapped_column(Float)
    provider_source: Mapped[str | None] = mapped_column(String(64))

    stock: Mapped["Stock"] = relationship(back_populates="prices")


# ─────────────────────────────────────────────────────────────────────────────
# daily_features
# ─────────────────────────────────────────────────────────────────────────────
class DailyFeature(Base):
    __tablename__ = "daily_features"
    __table_args__ = (
        UniqueConstraint("stock_id", "date", "feature_version", name="uq_feature_stock_date_ver"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"), nullable=False, index=True)
    date: Mapped[dt.date] = mapped_column(Date, nullable=False, index=True)
    feature_version: Mapped[str] = mapped_column(String(32), nullable=False)
    # Stored as JSONB blob — column-per-feature would be too wide to migrate easily
    features: Mapped[dict] = mapped_column(JSONB, nullable=False)
    computed_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

    stock: Mapped["Stock"] = relationship(back_populates="features")


# ─────────────────────────────────────────────────────────────────────────────
# daily_predictions
# ─────────────────────────────────────────────────────────────────────────────
class DailyPrediction(Base):
    __tablename__ = "daily_predictions"
    __table_args__ = (
        UniqueConstraint(
            "stock_id", "date", "model_version", name="uq_prediction_stock_date_model"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"), nullable=False, index=True)
    date: Mapped[dt.date] = mapped_column(Date, nullable=False, index=True)

    p_continue_3d: Mapped[float | None] = mapped_column(Float)
    p_continue_5d: Mapped[float | None] = mapped_column(Float)
    p_drawdown_5d: Mapped[float | None] = mapped_column(Float)
    p_mean_revert_3d: Mapped[float | None] = mapped_column(Float)

    classification: Mapped[str | None] = mapped_column(String(128))
    interpretation: Mapped[str | None] = mapped_column(Text)
    risk_score: Mapped[float | None] = mapped_column(Float)
    deception_score: Mapped[float | None] = mapped_column(Float)
    setup_quality_score: Mapped[float | None] = mapped_column(Float)
    confidence_bucket: Mapped[str | None] = mapped_column(String(32))
    explanation_flags: Mapped[list | None] = mapped_column(JSONB)

    model_version: Mapped[str] = mapped_column(String(32), nullable=False)
    feature_version: Mapped[str] = mapped_column(String(32), nullable=False)
    generated_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

    stock: Mapped["Stock"] = relationship(back_populates="predictions")
    outcome: Mapped["PredictionOutcome | None"] = relationship(
        back_populates="prediction", uselist=False
    )


# ─────────────────────────────────────────────────────────────────────────────
# daily_active_universe
# ─────────────────────────────────────────────────────────────────────────────
class DailyActiveUniverse(Base):
    __tablename__ = "daily_active_universe"
    __table_args__ = (UniqueConstraint("date", "stock_id", name="uq_universe_date_stock"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[dt.date] = mapped_column(Date, nullable=False, index=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"), nullable=False, index=True)
    rank_by_volume: Mapped[int | None] = mapped_column(Integer)
    rank_by_dollar_volume: Mapped[int | None] = mapped_column(Integer)
    is_in_scope: Mapped[bool] = mapped_column(Boolean, default=True)
    source: Mapped[str | None] = mapped_column(String(64))

    stock: Mapped["Stock"] = relationship(back_populates="universe_entries")


# ─────────────────────────────────────────────────────────────────────────────
# prediction_outcomes
# ─────────────────────────────────────────────────────────────────────────────
class PredictionOutcome(Base):
    __tablename__ = "prediction_outcomes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    prediction_id: Mapped[int] = mapped_column(
        ForeignKey("daily_predictions.id"), nullable=False, unique=True
    )
    realized_continue_3d: Mapped[bool | None] = mapped_column(Boolean)
    realized_continue_5d: Mapped[bool | None] = mapped_column(Boolean)
    realized_drawdown_5d: Mapped[bool | None] = mapped_column(Boolean)
    realized_mean_revert_3d: Mapped[bool | None] = mapped_column(Boolean)
    max_adverse_excursion_5d: Mapped[float | None] = mapped_column(Float)
    realized_at: Mapped[dt.datetime | None] = mapped_column(DateTime)

    prediction: Mapped["DailyPrediction"] = relationship(back_populates="outcome")


# ─────────────────────────────────────────────────────────────────────────────
# users
# ─────────────────────────────────────────────────────────────────────────────
class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(256), unique=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(256), nullable=False)
    plan_tier: Mapped[str] = mapped_column(String(32), default="free")  # "free" | "paid"
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    alerts: Mapped[list["Alert"]] = relationship(back_populates="user")


# ─────────────────────────────────────────────────────────────────────────────
# alerts
# ─────────────────────────────────────────────────────────────────────────────
class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    ticker: Mapped[str] = mapped_column(String(16), nullable=False)
    condition: Mapped[str] = mapped_column(String(128), nullable=False)
    threshold: Mapped[float | None] = mapped_column(Float)
    active_flag: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="alerts")
