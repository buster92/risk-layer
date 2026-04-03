"""
app/tests/conftest.py

Shared pytest fixtures.
Uses an in-memory SQLite database so tests run without PostgreSQL.
"""
from __future__ import annotations

import datetime as dt
import os

import pytest

# Override DB URL to in-memory SQLite before any app imports
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("MARKET_DATA_PROVIDER", "yfinance")


@pytest.fixture(scope="session")
def engine():
    from sqlalchemy import create_engine
    from app.db.models import Base

    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture
def db(engine):
    from sqlalchemy.orm import sessionmaker
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@pytest.fixture
def sample_stock(db):
    from app.db.models import Stock
    stock = Stock(ticker="TESTCO", name="Test Company Inc.", sector="Technology", industry="Software")
    db.add(stock)
    db.flush()
    return stock


@pytest.fixture
def sample_prices(db, sample_stock):
    from app.db.models import DailyPrice
    import datetime as dt

    prices = []
    base = 100.0
    for i in range(300):
        day = dt.date(2022, 1, 3) + dt.timedelta(days=i)
        if day.weekday() >= 5:
            continue
        price = base * (1 + (i % 5 - 2) * 0.005)
        p = DailyPrice(
            stock_id=sample_stock.id,
            date=day,
            open=price * 0.999,
            high=price * 1.01,
            low=price * 0.99,
            close=price,
            adj_close=price,
            volume=1_000_000 + i * 1000,
            dollar_volume=price * (1_000_000 + i * 1000),
        )
        db.add(p)
        prices.append(p)
    db.flush()
    return prices
