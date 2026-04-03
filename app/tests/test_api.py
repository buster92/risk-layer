"""
app/tests/test_api.py

API route tests using FastAPI's TestClient.
Uses mock database to avoid requiring a real PostgreSQL connection.
"""
from __future__ import annotations

import datetime as dt
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """Create a TestClient with mocked DB session."""
    from app.api.main import app
    return TestClient(app, raise_server_exceptions=False)


class TestHealthEndpoint:
    def test_health_returns_ok(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "version" in data
        assert "models_available" in data


class TestActiveBoard:
    @patch("app.api.routes.stocks.get_active_board")
    @patch("app.api.routes.stocks.last_closed_trading_day")
    def test_returns_board_shape(self, mock_date, mock_board, client):
        mock_date.return_value = dt.date(2024, 11, 15)
        mock_board.return_value = [
            {
                "rank": 1,
                "ticker": "NVDA",
                "company_name": "NVIDIA Corp",
                "sector": "Technology",
                "classification": "Favorable setup",
                "interpretation": "Trend aligned.",
                "confidence_bucket": "high",
                "p_continue_3d": 0.68,
                "p_continue_5d": 0.62,
                "p_drawdown_5d": 0.21,
                "p_mean_revert_3d": 0.18,
                "risk_score": 0.22,
                "deception_score": 0.22,
                "setup_quality_score": 0.71,
                "flags": ["Trend strength improving", "Sector aligned"],
            }
        ]

        resp = client.get("/v1/market/active")
        assert resp.status_code == 200
        data = resp.json()
        assert "stocks" in data
        assert data["count"] == 1
        assert data["stocks"][0]["ticker"] == "NVDA"


class TestStockAnalysis:
    @patch("app.api.routes.stocks.analyze_stock")
    @patch("app.api.routes.stocks.last_closed_trading_day")
    def test_analysis_returned(self, mock_date, mock_analyze, client):
        mock_date.return_value = dt.date(2024, 11, 15)
        mock_analyze.return_value = {
            "ticker": "TSLA",
            "date": "2024-11-15",
            "classification": "Breakout with exhaustion risk",
            "interpretation": "Heavy attention, weak follow-through.",
            "confidence_bucket": "medium",
            "probabilities": {
                "p_continue_3d": 0.31,
                "p_continue_5d": 0.28,
                "p_drawdown_5d": 0.49,
                "p_mean_revert_3d": 0.42,
            },
            "flags": ["Relative volume elevated", "Volatility expanding"],
            "risk_score": 0.62,
            "deception_score": 0.62,
            "setup_quality_score": 0.28,
        }

        resp = client.get("/v1/stocks/TSLA/analysis")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ticker"] == "TSLA"
        assert data["classification"] == "Breakout with exhaustion risk"
        assert "probabilities" in data
        assert "flags" in data

    @patch("app.api.routes.stocks.analyze_stock")
    @patch("app.api.routes.stocks.last_closed_trading_day")
    def test_404_when_not_found(self, mock_date, mock_analyze, client):
        mock_date.return_value = dt.date(2024, 11, 15)
        mock_analyze.return_value = None
        resp = client.get("/v1/stocks/ZZZZ/analysis")
        assert resp.status_code == 404


class TestTopRisks:
    @patch("app.api.routes.stocks.get_top_risks")
    @patch("app.api.routes.stocks.last_closed_trading_day")
    def test_top_risks_returns_list(self, mock_date, mock_risks, client):
        mock_date.return_value = dt.date(2024, 11, 15)
        mock_risks.return_value = []
        resp = client.get("/v1/market/top-risks")
        assert resp.status_code == 200
        assert "stocks" in resp.json()


class TestDigest:
    @patch("app.api.routes.stocks.build_daily_digest")
    @patch("app.api.routes.stocks.last_closed_trading_day")
    def test_digest_structure(self, mock_date, mock_digest, client):
        mock_date.return_value = dt.date(2024, 11, 15)
        mock_digest.return_value = {
            "date": "2024-11-15",
            "top_deceptive_moves": [],
            "strongest_continuation_profiles": [],
            "prior_session_outcomes": [],
            "summary": "No data yet.",
        }
        resp = client.get("/v1/digest/daily")
        assert resp.status_code == 200
        data = resp.json()
        assert "top_deceptive_moves" in data
        assert "strongest_continuation_profiles" in data
