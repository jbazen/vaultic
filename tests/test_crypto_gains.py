"""Tests for the Crypto Capital Gains API endpoints.

Covers:
  - Auth guards on all crypto endpoints
  - POST /api/crypto/sync-trades (mocked Coinbase — env vars absent)
  - GET /api/crypto/trades with date filtering
  - POST /api/crypto/calculate-gains — FIFO lot matching
  - GET /api/crypto/gains/{year} — tax year summary
  - GET /api/crypto/lots — cost basis lots
  - FIFO correctness: short-term vs long-term classification, partial lot splits
"""


# ── Auth guard tests ──────────────────────────────────────────────────────────

class TestCryptoAuth:
    """All crypto endpoints require a valid JWT."""

    def test_sync_trades_requires_auth(self, client):
        r = client.post("/api/crypto/sync-trades")
        assert r.status_code == 401 or r.status_code == 403

    def test_trades_requires_auth(self, client):
        r = client.get("/api/crypto/trades")
        assert r.status_code == 401 or r.status_code == 403

    def test_calculate_gains_requires_auth(self, client):
        r = client.post("/api/crypto/calculate-gains")
        assert r.status_code == 401 or r.status_code == 403

    def test_gains_requires_auth(self, client):
        r = client.get("/api/crypto/gains/2025")
        assert r.status_code == 401 or r.status_code == 403

    def test_lots_requires_auth(self, client):
        r = client.get("/api/crypto/lots")
        assert r.status_code == 401 or r.status_code == 403


# ── Sync trades endpoint ─────────────────────────────────────────────────────

class TestSyncTrades:
    """POST /api/crypto/sync-trades — requires Coinbase env vars."""

    def test_sync_without_valid_keys_returns_error(self, client, auth_headers):
        """Missing or invalid Coinbase keys should return 400 (not configured) or 502 (auth failed)."""
        r = client.post("/api/crypto/sync-trades", headers=auth_headers)
        assert r.status_code in (400, 502)


# ── Helper: seed trades directly ──────────────────────────────────────────────

def _seed_trades(conn):
    """Insert test trades for FIFO calculation tests.

    Creates a realistic scenario:
      - BUY 1.0 BTC @ $30,000 on 2024-01-15 (fee $10)
      - BUY 0.5 BTC @ $40,000 on 2024-06-01 (fee $5)
      - SELL 0.8 BTC @ $50,000 on 2025-03-01 (fee $8) — short-term (< 365 days from 2024-06-01 lot)
      - BUY 2.0 ETH @ $2,000 on 2024-03-01 (fee $3)
      - SELL 1.0 ETH @ $3,000 on 2025-04-01 (fee $4) — long-term (> 365 days)
    """
    trades = [
        ("t1", "o1", "BTC-USD", "BUY",  1.0,  30000, 10, "2024-01-15T10:00:00Z"),
        ("t2", "o2", "BTC-USD", "BUY",  0.5,  40000, 5,  "2024-06-01T10:00:00Z"),
        ("t3", "o3", "BTC-USD", "SELL", 0.8,  50000, 8,  "2025-03-01T10:00:00Z"),
        ("t4", "o4", "ETH-USD", "BUY",  2.0,  2000,  3,  "2024-03-01T10:00:00Z"),
        ("t5", "o5", "ETH-USD", "SELL", 1.0,  3000,  4,  "2025-04-01T10:00:00Z"),
    ]
    for t in trades:
        conn.execute("""
            INSERT OR IGNORE INTO crypto_trades
                (trade_id, order_id, product_id, side, size, price, fee, trade_time)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, t)
    conn.commit()


# ── Trade listing tests ───────────────────────────────────────────────────────

class TestListTrades:
    """GET /api/crypto/trades — list stored trades."""

    def test_empty_trades(self, client, auth_headers):
        r = client.get("/api/crypto/trades", headers=auth_headers)
        assert r.status_code == 200
        # May return trades from other tests (session-scoped DB), so just check format
        assert isinstance(r.json(), list)

    def test_trades_after_seed(self, client, auth_headers):
        from api.database import get_db
        with get_db() as conn:
            _seed_trades(conn)
        r = client.get("/api/crypto/trades", headers=auth_headers)
        assert r.status_code == 200
        trades = r.json()
        assert len(trades) >= 5

    def test_trades_date_filter(self, client, auth_headers):
        """Filter trades to 2025 only — should get the two SELL trades."""
        r = client.get("/api/crypto/trades?start_date=2025-01-01&end_date=2025-12-31", headers=auth_headers)
        assert r.status_code == 200
        trades = r.json()
        assert all(t["trade_time"] >= "2025-01-01" for t in trades)

    def test_trades_limit(self, client, auth_headers):
        r = client.get("/api/crypto/trades?limit=2", headers=auth_headers)
        assert r.status_code == 200
        assert len(r.json()) <= 2


# ── FIFO calculation tests ───────────────────────────────────────────────────

class TestCalculateGains:
    """POST /api/crypto/calculate-gains — FIFO lot matching."""

    def test_calculate_returns_summary(self, client, auth_headers):
        r = client.post("/api/crypto/calculate-gains", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert data["buys"] >= 3  # 3 BUY trades seeded
        assert data["sells"] >= 2  # 2 SELL trades seeded
        assert data["gains_computed"] >= 2

    def test_fifo_btc_lot_matching(self, client, auth_headers):
        """BTC SELL of 0.8 should match FIFO: 0.8 from first lot (1.0 @ $30k).

        Cost basis: 0.8 * ($30,000 + $10/1.0) = 0.8 * $30,010 = $24,008
        Proceeds: (0.8/0.8) * ($50,000 * 0.8 - $8) = $39,992
        Gain: $39,992 - $24,008 = $15,984
        """
        r = client.get("/api/crypto/gains/2025", headers=auth_headers)
        data = r.json()
        btc_gains = [g for g in data["transactions"] if g["currency"] == "BTC"]
        assert len(btc_gains) >= 1
        # The first BTC lot was bought 2024-01-15, sold 2025-03-01 = 410 days → long-term
        assert btc_gains[0]["gain_type"] == "long_term"
        assert btc_gains[0]["gain_loss"] > 0  # Profitable sale

    def test_fifo_eth_long_term(self, client, auth_headers):
        """ETH bought 2024-03-01, sold 2025-04-01 = 396 days → long-term."""
        r = client.get("/api/crypto/gains/2025", headers=auth_headers)
        data = r.json()
        eth_gains = [g for g in data["transactions"] if g["currency"] == "ETH"]
        assert len(eth_gains) >= 1
        assert eth_gains[0]["gain_type"] == "long_term"
        assert eth_gains[0]["gain_loss"] > 0

    def test_remaining_lots(self, client, auth_headers):
        """After sells, should have remaining BTC and ETH lots."""
        r = client.get("/api/crypto/lots", headers=auth_headers)
        lots = r.json()
        assert len(lots) >= 2

        # BTC: 1.0 bought, 0.8 sold → 0.2 remaining from lot 1; 0.5 untouched in lot 2
        btc_lots = [l for l in lots if l["currency"] == "BTC"]
        btc_remaining = sum(l["quantity_remaining"] for l in btc_lots)
        assert abs(btc_remaining - 0.7) < 0.001  # 0.2 + 0.5

        # ETH: 2.0 bought, 1.0 sold → 1.0 remaining
        eth_lots = [l for l in lots if l["currency"] == "ETH"]
        eth_remaining = sum(l["quantity_remaining"] for l in eth_lots)
        assert abs(eth_remaining - 1.0) < 0.001


# ── Gains by year tests ──────────────────────────────────────────────────────

class TestGainsByYear:
    """GET /api/crypto/gains/{year} — tax year summary."""

    def test_gains_2025_summary(self, client, auth_headers):
        r = client.get("/api/crypto/gains/2025", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        assert data["year"] == 2025
        assert "summary" in data
        s = data["summary"]
        assert s["transaction_count"] >= 2
        assert s["net_gain_loss"] > 0  # Both trades were profitable
        assert s["total_proceeds"] > 0
        assert s["total_cost_basis"] > 0

    def test_gains_empty_year(self, client, auth_headers):
        """Year with no sales should return empty."""
        r = client.get("/api/crypto/gains/2020", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        assert data["summary"]["transaction_count"] == 0
        assert data["summary"]["net_gain_loss"] == 0

    def test_gains_short_long_breakdown(self, client, auth_headers):
        """Summary should separately report short-term and long-term."""
        r = client.get("/api/crypto/gains/2025", headers=auth_headers)
        s = r.json()["summary"]
        # All our test trades are long-term (held > 365 days)
        assert "short_term_gains" in s
        assert "long_term_gains" in s
        assert "short_term_net" in s
        assert "long_term_net" in s


# ── Lots endpoint tests ──────────────────────────────────────────────────────

class TestCryptoLots:
    """GET /api/crypto/lots — cost basis lots."""

    def test_lots_all(self, client, auth_headers):
        r = client.get("/api/crypto/lots", headers=auth_headers)
        assert r.status_code == 200
        lots = r.json()
        assert isinstance(lots, list)
        assert len(lots) >= 3  # 2 BTC lots + 1 ETH lot

    def test_lots_filter_by_currency(self, client, auth_headers):
        r = client.get("/api/crypto/lots?currency=BTC", headers=auth_headers)
        assert r.status_code == 200
        lots = r.json()
        assert all(l["currency"] == "BTC" for l in lots)

    def test_lots_filter_eth(self, client, auth_headers):
        r = client.get("/api/crypto/lots?currency=ETH", headers=auth_headers)
        assert r.status_code == 200
        lots = r.json()
        assert all(l["currency"] == "ETH" for l in lots)
        assert len(lots) >= 1


# ── FIFO partial lot split test ───────────────────────────────────────────────

class TestFifoPartialSplit:
    """Verify that partial lot consumption works correctly.

    The BTC sell of 0.8 units should consume 0.8 from the first lot (qty 1.0),
    leaving 0.2 remaining in that lot and the second lot (qty 0.5) untouched.
    """

    def test_first_lot_partially_consumed(self, client, auth_headers):
        r = client.get("/api/crypto/lots?currency=BTC", headers=auth_headers)
        lots = r.json()
        # Sort by acquisition date to find first lot
        lots.sort(key=lambda l: l["acquisition_date"])
        first_lot = lots[0]
        assert first_lot["acquisition_date"] == "2024-01-15"
        assert abs(first_lot["quantity"] - 1.0) < 0.001
        assert abs(first_lot["quantity_remaining"] - 0.2) < 0.001

    def test_second_lot_untouched(self, client, auth_headers):
        r = client.get("/api/crypto/lots?currency=BTC", headers=auth_headers)
        lots = r.json()
        lots.sort(key=lambda l: l["acquisition_date"])
        second_lot = lots[1]
        assert second_lot["acquisition_date"] == "2024-06-01"
        assert abs(second_lot["quantity_remaining"] - 0.5) < 0.001
