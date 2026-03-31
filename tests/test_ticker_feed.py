"""Tests for ticker feed router — quotes, news, and summary endpoints."""
import sqlite3
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta


class TestTickerFeedAuth:
    """Auth guards on all feed endpoints."""

    def test_tickers_requires_auth(self, client):
        assert client.get("/api/feed/tickers").status_code == 401

    def test_quotes_requires_auth(self, client):
        assert client.get("/api/feed/quotes").status_code == 401

    def test_news_requires_auth(self, client):
        assert client.get("/api/feed/news").status_code == 401

    def test_summary_requires_auth(self, client):
        assert client.get("/api/feed/summary").status_code == 401

    def test_refresh_quotes_requires_auth(self, client):
        assert client.post("/api/feed/quotes/refresh").status_code == 401

    def test_refresh_news_requires_auth(self, client):
        assert client.post("/api/feed/news/refresh").status_code == 401


class TestTickerExtraction:
    """Ticker symbol extraction from holdings."""

    def test_tickers_empty_db(self, client, auth_headers):
        resp = client.get("/api/feed/tickers", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["crypto"] == []
        assert data["equity"] == []

    def test_tickers_from_coinbase_accounts(self, client, auth_headers):
        import api.database as db_module
        with db_module.get_db() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO accounts (plaid_account_id, name, type, subtype, source, is_active) "
                "VALUES ('cb_btc', 'BTC Wallet', 'crypto', 'BTC', 'coinbase', 1)"
            )
            conn.execute(
                "INSERT OR IGNORE INTO accounts (plaid_account_id, name, type, subtype, source, is_active) "
                "VALUES ('cb_eth', 'ETH Wallet', 'crypto', 'ETH', 'coinbase', 1)"
            )
            conn.commit()

        resp = client.get("/api/feed/tickers", headers=auth_headers)
        data = resp.json()
        assert "BTC" in data["crypto"]
        assert "ETH" in data["crypto"]

    def test_tickers_excludes_stablecoins(self, client, auth_headers):
        import api.database as db_module
        with db_module.get_db() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO accounts (plaid_account_id, name, type, subtype, source, is_active) "
                "VALUES ('cb_usdc', 'USDC', 'crypto', 'USDC', 'coinbase', 1)"
            )
            conn.commit()

        resp = client.get("/api/feed/tickers", headers=auth_headers)
        data = resp.json()
        assert "USDC" not in data["crypto"]


class TestQuotes:
    """Quote caching and retrieval."""

    def test_quotes_empty(self, client, auth_headers):
        resp = client.get("/api/feed/quotes", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["quotes"] == [] or isinstance(resp.json()["quotes"], list)

    def test_quotes_cached_data(self, client, auth_headers):
        import api.database as db_module
        now = datetime.utcnow().isoformat()
        with db_module.get_db() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO ticker_quotes (symbol, asset_type, price, change_pct, source, fetched_at) "
                "VALUES ('BTC', 'crypto', 97420.50, 2.34, 'coinbase', ?)", (now,)
            )
            conn.commit()

        resp = client.get("/api/feed/quotes", headers=auth_headers)
        data = resp.json()
        btc = next((q for q in data["quotes"] if q["symbol"] == "BTC"), None)
        assert btc is not None
        assert btc["price"] == 97420.50
        assert btc["change_pct"] == 2.34

    @patch("api.routers.ticker_feed._fetch_crypto_quotes")
    @patch("api.routers.ticker_feed._fetch_crypto_changes")
    @patch("api.routers.ticker_feed._fetch_equity_quotes")
    def test_refresh_quotes(self, mock_equity, mock_changes, mock_crypto, client, auth_headers):
        mock_crypto.return_value = [
            {"symbol": "SOL", "asset_type": "crypto", "price": 185.50, "change_pct": None, "source": "coinbase"}
        ]
        mock_changes.return_value = {"SOL": 3.21}
        mock_equity.return_value = []

        resp = client.post("/api/feed/quotes/refresh", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

        # Verify it was cached
        resp2 = client.get("/api/feed/quotes", headers=auth_headers)
        sol = next((q for q in resp2.json()["quotes"] if q["symbol"] == "SOL"), None)
        assert sol is not None
        assert sol["price"] == 185.50
        assert sol["change_pct"] == 3.21


class TestNews:
    """News caching and retrieval."""

    def test_news_empty(self, client, auth_headers):
        resp = client.get("/api/feed/news", headers=auth_headers)
        assert resp.status_code == 200
        assert isinstance(resp.json()["articles"], list)

    def test_news_cached_data(self, client, auth_headers):
        import api.database as db_module
        now = datetime.utcnow().isoformat()
        with db_module.get_db() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO news_articles (title, url, source_name, snippet, relevance, fetched_at) "
                "VALUES ('BTC hits 100k', 'https://example.com/btc', 'example.com', 'Bitcoin surged...', 'crypto', ?)",
                (now,)
            )
            conn.commit()

        resp = client.get("/api/feed/news", headers=auth_headers)
        data = resp.json()
        assert any(a["title"] == "BTC hits 100k" for a in data["articles"])

    @patch("api.routers.ticker_feed._fetch_news")
    def test_refresh_news(self, mock_fetch, client, auth_headers):
        mock_fetch.return_value = [
            {"title": "Test Article", "url": "https://example.com/test", "snippet": "test",
             "source_name": "example.com", "relevance": "macro", "published_at": None}
        ]
        resp = client.post("/api/feed/news/refresh", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["ok"] is True


class TestSummary:
    """Combined summary endpoint."""

    def test_summary_returns_all_sections(self, client, auth_headers):
        with patch("api.routers.ticker_feed._do_refresh_quotes"):
            with patch("api.routers.ticker_feed._do_refresh_news"):
                resp = client.get("/api/feed/summary", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "tickers" in data
        assert "quotes" in data
        assert "news" in data
        assert "crypto" in data["tickers"]
        assert "equity" in data["tickers"]
