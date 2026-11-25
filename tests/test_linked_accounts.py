"""Account history links (#181): Parker (Investor360) accounts linked to the
LPL (Plaid) accounts they moved to.

Every test runs on its own in-memory DB (make_isolated_db), so sums over whole
tables are exact, and runs twice: with plain text dates and with
PARSE_DECLTYPES (DATE columns as datetime.date), as in production.
"""
import sqlite3
from datetime import date, timedelta

import pytest

from tests.conftest import make_isolated_db

PARKER = "Parker Financial (Commonwealth/NFS)"
LPL = "LPL Financial"


def _days_ago(n: int) -> str:
    return (date.today() - timedelta(days=n)).isoformat()


@pytest.fixture(params=[0, sqlite3.PARSE_DECLTYPES], ids=["text-dates", "decltypes"])
def db(monkeypatch, request):
    conn, get = make_isolated_db(request.param)
    import api.routers.accounts as accounts_router
    import api.routers.linked_accounts as linked_router
    import api.routers.net_worth as net_worth_router
    import api.sync as sync
    for mod in (accounts_router, linked_router, net_worth_router, sync):
        monkeypatch.setattr(mod, "get_db", get)
    yield conn
    conn.close()


def _account(conn, number, inst, source, active, subtype="ira", name=None):
    cur = conn.execute(
        "INSERT INTO accounts (plaid_account_id, account_number, name, type, subtype, "
        "institution_name, source, is_manual, is_active, mask) "
        "VALUES (?, ?, ?, 'investment', ?, ?, ?, 0, ?, ?)",
        (f"pid-{number}", number, name or number, subtype, inst, source, active, number[-4:]),
    )
    return cur.lastrowid


def _balance(conn, number, day, value):
    acct_id = conn.execute("SELECT id FROM accounts WHERE account_number = ?", (number,)).fetchone()[0]
    conn.execute(
        "INSERT INTO account_balances (account_id, account_number, current, snapped_at) VALUES (?, ?, ?, ?)",
        (acct_id, number, value, day),
    )


def _i360(conn, number, day, symbol, qty, value, category, asset_class="Domestic Equity"):
    acct_id = conn.execute("SELECT id FROM accounts WHERE account_number = ?", (number,)).fetchone()[0]
    conn.execute(
        "INSERT INTO i360_holdings (account_id, account_number, snapped_at, symbol, description, "
        "quantity, value_dollars, asset_category, primary_asset_class, i360_holding_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (acct_id, number, day, symbol, f"{symbol} fund", qty, value, category, asset_class,
         abs(hash((number, symbol, day))) % 10**9),
    )


def _plaid(conn, number, day, ticker, qty, value, sec_type="mutual fund"):
    sec_id = f"sec-{ticker or 'cash'}-{sec_type}"
    conn.execute(
        "INSERT OR IGNORE INTO plaid_securities (security_id, name, ticker_symbol, type) VALUES (?, ?, ?, ?)",
        (sec_id, f"{ticker or 'Cash'} name", ticker, sec_type),
    )
    acct_id = conn.execute("SELECT id FROM accounts WHERE account_number = ?", (number,)).fetchone()[0]
    conn.execute(
        "INSERT INTO plaid_holdings (account_id, account_number, security_id, quantity, "
        "institution_value, iso_currency_code, snapped_at) VALUES (?, ?, ?, ?, ?, 'USD', ?)",
        (acct_id, number, sec_id, qty, value, day),
    )


@pytest.fixture
def moved(db):
    """Three Parker accounts and the three LPL accounts they moved to, plus a decoy.

    P1 -> S1 and P2 -> S2 hold the same funds and share counts (S2 also holds
    a fund Parker never had); P3 and S3 are empty. V (Vanguard) shares a ticker with P1 but not the share counts.
    """
    old, gap, new = _days_ago(30), _days_ago(20), _days_ago(5)
    for n in ("P1", "P2", "P3"):
        _account(db, n, PARKER, "investor360", 0)
    for n in ("S1", "S2", "S3"):
        _account(db, n, LPL, "plaid", 1)
    _account(db, "V", "Vanguard", "plaid", 1, subtype="401k")

    _i360(db, "P1", old, "AAA", 10, 1000, "Large-Cap Growth")
    _i360(db, "P1", old, "BBB", 20, 2000, "Emerging Markets", "International Equity")
    _i360(db, "P1", old, "QCASH", 50, 50, "Cash and Equivalents", "Cash and Equivalents")
    _i360(db, "P2", old, "AAA", 5, 500, "Large-Cap Growth")
    _i360(db, "P2", old, "CCC", 7, 700, "Small-Cap Blend")
    _i360(db, "P2", old, "DDD", 4, 90, "Large-Cap Value")

    _plaid(db, "S1", new, "AAA", 10, 1100)
    _plaid(db, "S1", new, "BBB", 20, 2100)
    _plaid(db, "S1", new, None, 60, 60, sec_type="cash")
    _plaid(db, "S2", new, "AAA", 5, 550)
    _plaid(db, "S2", new, "CCC", 7.0001, 720)
    _plaid(db, "S2", new, "DDD", 4, 100)
    _plaid(db, "S2", new, "ZZZ", 3, 30, sec_type="etf")
    _plaid(db, "V", new, "AAA", 99, 9900)

    _balance(db, "P1", old, 3050.0)
    _balance(db, "P2", old, 1200.0)
    _balance(db, "P3", _days_ago(60), 8000.0)
    _balance(db, "P3", old, 0.0)
    _balance(db, "S1", new, 3260.0)
    _balance(db, "S2", new, 1300.0)
    _balance(db, "S3", new, 0.0)
    _balance(db, "V", gap, 9000.0)
    _balance(db, "V", new, 9900.0)
    db.commit()
    return {"old": old, "gap": gap, "new": new}


def _links(conn):
    return {(r["predecessor_account_number"], r["successor_account_number"], r["method"])
            for r in conn.execute("SELECT * FROM account_links WHERE unlinked_at IS NULL")}


def _link_all(conn):
    from api.account_links import auto_link_accounts
    auto_link_accounts(conn)
    conn.commit()


# ── Matching ───────────────────────────────────────────────────────────────

class TestAutoLink:
    def test_links_by_holdings_then_the_empty_pair(self, db, moved):
        from api.account_links import auto_link_accounts
        assert auto_link_accounts(db) == 3
        assert _links(db) == {("P1", "S1", "auto-holdings"), ("P2", "S2", "auto-holdings"),
                              ("P3", "S3", "auto-empty")}

    def test_idempotent(self, db, moved):
        from api.account_links import auto_link_accounts
        auto_link_accounts(db)
        assert auto_link_accounts(db) == 0
        assert db.execute("SELECT COUNT(*) FROM account_links").fetchone()[0] == 3

    def test_never_relinks_what_the_owner_unlinked(self, db, moved):
        from api.account_links import auto_link_accounts
        auto_link_accounts(db)
        db.execute("UPDATE account_links SET unlinked_at = CURRENT_TIMESTAMP WHERE predecessor_account_number = 'P1'")
        assert auto_link_accounts(db) == 0
        assert ("P1", "S1", "auto-holdings") not in _links(db)

    def test_no_match_below_threshold_or_on_a_tie(self, db, moved):
        from api.account_links import auto_link_accounts
        # S1 now holds only one of P1's two funds: 50% < 75%
        db.execute("DELETE FROM plaid_holdings WHERE account_number = 'S1' AND security_id LIKE 'sec-BBB%'")
        # S2's twin (same holdings) makes P2's best match a tie
        _account(db, "S2B", LPL, "plaid", 1)
        _plaid(db, "S2B", moved["new"], "AAA", 5, 550)
        _plaid(db, "S2B", moved["new"], "CCC", 7, 700)
        _plaid(db, "S2B", moved["new"], "DDD", 4, 100)
        _plaid(db, "S2B", moved["new"], "ZZZ", 3, 30, sec_type="etf")
        auto_link_accounts(db)
        linked = {p for p, _, _ in _links(db)}
        assert "P1" not in linked and "P2" not in linked
        # No holdings match at LPL, so the empty pair is not linked either
        assert "P3" not in linked

    def test_empty_pair_needs_a_zero_balance(self, db, moved):
        from api.account_links import auto_link_accounts
        _balance(db, "S3", _days_ago(1), 5.0)
        auto_link_accounts(db)
        assert "P3" not in {p for p, _, _ in _links(db)}

    def test_active_parker_account_is_not_matched(self, db, moved):
        from api.account_links import auto_link_accounts
        db.execute("UPDATE accounts SET is_active = 1 WHERE account_number = 'P1'")
        auto_link_accounts(db)
        assert "P1" not in {p for p, _, _ in _links(db)}

    def test_sync_all_links_new_accounts(self, db, moved, monkeypatch):
        import api.sync as sync
        monkeypatch.setattr("api.coinbase_sync.sync_coinbase", lambda: None)
        monkeypatch.setattr("api.push.notify_stale_i360", lambda: None)
        sync.sync_all()
        assert len(_links(db)) == 3


# ── Net worth snapshot ─────────────────────────────────────────────────────

class TestSnapshot:
    def _invested(self, db, day):
        return db.execute("SELECT invested FROM net_worth_snapshots WHERE snapped_at = ?", (day,)).fetchone()[0]

    def test_successor_without_balance_counts_its_predecessor(self, db, moved):
        from api.sync import _take_net_worth_snapshot
        _link_all(db)
        db.execute("DELETE FROM account_balances WHERE account_number = 'S2'")
        db.commit()
        _take_net_worth_snapshot("2099-01-01")
        # S1 + P2 (S2 has no balance yet) + S3 + V
        assert self._invested(db, "2099-01-01") == pytest.approx(3260 + 1200 + 0 + 9900)

    def test_active_predecessor_is_not_counted_twice(self, db, moved):
        from api.sync import _take_net_worth_snapshot
        _link_all(db)
        db.execute("UPDATE accounts SET is_active = 1 WHERE account_number = 'P1'")
        db.commit()
        _take_net_worth_snapshot("2099-01-01")
        assert self._invested(db, "2099-01-01") == pytest.approx(3260 + 1300 + 0 + 9900)

    def test_unlinked_pair_counts_only_the_active_account(self, db, moved):
        from api.sync import _take_net_worth_snapshot
        _link_all(db)
        db.execute("UPDATE account_links SET unlinked_at = CURRENT_TIMESTAMP")
        db.execute("DELETE FROM account_balances WHERE account_number = 'S2'")
        db.commit()
        _take_net_worth_snapshot("2099-01-01")
        assert self._invested(db, "2099-01-01") == pytest.approx(3260 + 0 + 9900)


class TestCutoverRepair:
    def _snap(self, db, day, invested):
        db.execute("INSERT INTO net_worth_snapshots (snapped_at, total, invested) VALUES (?, ?, ?)",
                   (day, invested + 100, invested))

    def test_adds_pairs_counted_by_neither_account_once(self, db, moved):
        from api.account_links import repair_cutover_snapshots
        _link_all(db)
        mid = _days_ago(10)
        # S2's first balance comes after `mid`; S1's is moved before it
        db.execute("UPDATE account_balances SET snapped_at = ? WHERE account_number = 'S1'", (_days_ago(12),))
        self._snap(db, mid, 5000.0)
        added = repair_cutover_snapshots(db, dates=(mid,), reason="test")
        # P2 (1200) and P3 (0) were counted by neither; P1's successor had a balance
        assert added == pytest.approx(1200.0)
        row = db.execute("SELECT total, invested FROM net_worth_snapshots WHERE snapped_at = ?", (mid,)).fetchone()
        assert (row["invested"], row["total"]) == (pytest.approx(6200.0), pytest.approx(6300.0))
        # One row per pair looked at; only P2's carries an amount
        assert dict(db.execute("SELECT reason, amount FROM net_worth_snapshot_repairs WHERE snapped_at = ?",
                               (mid,)).fetchall()) == {"test P1": 0, "test P2": 1200.0, "test P3": 0}
        # Second run: already recorded, nothing changes
        assert repair_cutover_snapshots(db, dates=(mid,), reason="test") == 0
        assert db.execute("SELECT invested FROM net_worth_snapshots WHERE snapped_at = ?",
                          (mid,)).fetchone()[0] == pytest.approx(6200.0)

    def test_nothing_to_add_records_zero_per_pair(self, db, moved):
        from api.account_links import repair_cutover_snapshots
        _link_all(db)
        self._snap(db, moved["new"], 5000.0)
        assert repair_cutover_snapshots(db, dates=(moved["new"],), reason="test") == 0
        assert db.execute("SELECT COUNT(*), SUM(amount) FROM net_worth_snapshot_repairs").fetchone()[:] == (3, 0)

    def test_without_links_nothing_is_added(self, db, moved):
        from api.account_links import repair_cutover_snapshots
        mid = _days_ago(10)
        self._snap(db, mid, 5000.0)
        assert repair_cutover_snapshots(db, dates=(mid,), reason="test") == 0


# ── History endpoints ──────────────────────────────────────────────────────

class TestHistory:
    def test_balances_start_with_the_predecessor(self, db, moved, client, auth_headers):
        _link_all(db)
        _balance(db, "P1", moved["new"], 9999.0)   # on/after the successor's start: ignored
        db.commit()
        s1 = db.execute("SELECT id FROM accounts WHERE account_number = 'S1'").fetchone()[0]
        rows = client.get(f"/api/accounts/{s1}/balances?days=365", headers=auth_headers).json()
        assert [(r["snapped_at"], r["current"]) for r in rows] == [
            (moved["old"], 3050.0), (moved["new"], 3260.0)]

    def test_unlinked_balances_are_the_accounts_own(self, db, moved, client, auth_headers):
        _link_all(db)
        db.execute("UPDATE account_links SET unlinked_at = CURRENT_TIMESTAMP")
        db.commit()
        s1 = db.execute("SELECT id FROM accounts WHERE account_number = 'S1'").fetchone()[0]
        rows = client.get(f"/api/accounts/{s1}/balances?days=365", headers=auth_headers).json()
        assert [r["current"] for r in rows] == [3260.0]

    def test_performance_carries_parker_across_the_gap(self, db, moved, client, auth_headers):
        _link_all(db)
        by_date = {r["snapped_at"]: r["total_value"] for r in client.get(
            "/api/accounts/portfolio/performance?days=365", headers=auth_headers).json()}
        assert by_date[moved["old"]] == pytest.approx(3050 + 1200 + 0)
        # Gap day (only V has a point): Parker's values carry forward, no dip
        assert by_date[moved["gap"]] == pytest.approx(3050 + 1200 + 0 + 9000)
        assert by_date[moved["new"]] == pytest.approx(3260 + 1300 + 0 + 9900)

    def test_performance_unlinked_drops_parker_after_its_last_date(self, db, moved, client, auth_headers):
        by_date = {r["snapped_at"]: r["total_value"] for r in client.get(
            "/api/accounts/portfolio/performance?days=365", headers=auth_headers).json()}
        assert by_date[moved["gap"]] == pytest.approx(9000)

    def test_account_list_names_the_linked_account(self, db, moved, client, auth_headers):
        _link_all(db)
        rows = {a["coinbase_uuid"]: a for a in client.get("/api/accounts", headers=auth_headers).json()}
        s1, v = rows["pid-S1"], rows["pid-V"]
        assert s1["link_active"] == 1 and s1["linked_from_name"] == "P1"
        assert s1["linked_until"] == moved["old"]
        assert v["link_id"] is None and v["link_active"] is None


# ── Allocation, summary, link state ────────────────────────────────────────

class TestLinkedEndpoints:
    def test_allocation_uses_parker_categories(self, db, moved, client, auth_headers):
        s2 = db.execute("SELECT id FROM accounts WHERE account_number = 'S2'").fetchone()[0]
        data = client.get(f"/api/linked-accounts/allocation/{s2}", headers=auth_headers).json()
        assert {c["asset_name"]: c["market_value"] for c in data["categories"]} == {
            "Small-Cap Blend": 720, "Large-Cap Growth": 550, "Large-Cap Value": 100, "ETF": 30}
        s1 = db.execute("SELECT id FROM accounts WHERE account_number = 'S1'").fetchone()[0]
        data = client.get(f"/api/linked-accounts/allocation/{s1}", headers=auth_headers).json()
        assert {c["asset_name"]: c["market_value"] for c in data["asset_classes"]} == {
            "International Equity": 2100, "Domestic Equity": 1100, "Cash and Equivalents": 60}

    def test_allocation_unknown_account_is_404(self, db, client, auth_headers):
        assert client.get("/api/linked-accounts/allocation/999999", headers=auth_headers).status_code == 404

    def test_summary(self, db, moved, client, auth_headers):
        _link_all(db)
        db.execute("INSERT INTO i360_balance_history (balance_date, market_value, net_investment) "
                   "VALUES ('2020-01-31', 100, 90), (?, 4250, 3000)", (moved["old"],))
        db.commit()
        groups = client.get("/api/linked-accounts/summary", headers=auth_headers).json()["groups"]
        assert len(groups) == 1
        g = groups[0]
        assert g["institution"] == LPL and g["parker_only"] is True
        assert g["predecessor_as_of"] == moved["old"]
        assert g["total_value"] == pytest.approx(3260 + 1300 + 0)
        assert g["top_holdings"][0] == {"symbol": "BBB", "description": "BBB name", "value_dollars": 2100}
        aaa = next(h for h in g["top_holdings"] if h["symbol"] == "AAA")
        assert aaa["value_dollars"] == 1650
        hist = [(h["balance_date"], h["market_value"], h["net_investment"]) for h in g["balance_history"]]
        assert hist == [("2020-01-31", 100, 90), (moved["old"], 4250, 3000),
                        (moved["new"], pytest.approx(4560.0), None)]

    def test_summary_without_links_is_empty(self, db, moved, client, auth_headers):
        assert client.get("/api/linked-accounts/summary", headers=auth_headers).json() == {"groups": []}

    def test_unlink_and_relink(self, db, moved, client, auth_headers):
        _link_all(db)
        link_id = db.execute(
            "SELECT id FROM account_links WHERE predecessor_account_number = 'P1'").fetchone()[0]
        r = client.patch(f"/api/linked-accounts/{link_id}", json={"active": False}, headers=auth_headers)
        assert r.json() == {"ok": True, "active": False}
        assert ("P1", "S1", "auto-holdings") not in _links(db)
        # Account data is untouched
        assert db.execute("SELECT COUNT(*) FROM account_balances WHERE account_number = 'P1'").fetchone()[0] == 1
        client.patch(f"/api/linked-accounts/{link_id}", json={"active": True}, headers=auth_headers)
        assert ("P1", "S1", "auto-holdings") in _links(db)

    def test_unknown_link_is_404(self, db, client, auth_headers):
        r = client.patch("/api/linked-accounts/999999", json={"active": False}, headers=auth_headers)
        assert r.status_code == 404

    @pytest.mark.parametrize("method,path", [
        ("get", "/api/linked-accounts/summary"),
        ("get", "/api/linked-accounts/allocation/1"),
        ("patch", "/api/linked-accounts/1"),
    ])
    def test_requires_auth(self, client, method, path):
        kwargs = {"json": {"active": True}} if method == "patch" else {}
        assert getattr(client, method)(path, **kwargs).status_code == 401


# ── Review follow-ups ──────────────────────────────────────────────────────

class TestMatchingEdges:
    def test_superset_account_does_not_steal_or_tie(self, db):
        """Pb holds everything Pa holds plus one more fund; each still finds its own twin."""
        from api.account_links import auto_link_accounts
        day = _days_ago(5)
        for n in ("PA", "PB"):
            _account(db, n, PARKER, "investor360", 0)
        for n in ("SA", "SB"):
            _account(db, n, LPL, "plaid", 1)
        for acct, extra in (("PA", False), ("PB", True)):
            _i360(db, acct, day, "AAA", 10, 100, "Large-Cap Growth")
            _i360(db, acct, day, "BBB", 20, 200, "Large-Cap Value")
            if extra:
                _i360(db, acct, day, "CCC", 5, 50, "Small-Cap Blend")
        for acct, extra in (("SA", False), ("SB", True)):
            _plaid(db, acct, day, "AAA", 10, 100)
            _plaid(db, acct, day, "BBB", 20, 200)
            if extra:
                _plaid(db, acct, day, "CCC", 5, 50)
        assert auto_link_accounts(db) == 2
        assert {(p, s) for p, s, _ in _links(db)} == {("PA", "SA"), ("PB", "SB")}

    def test_one_shared_fund_is_not_a_match(self, db):
        from api.account_links import auto_link_accounts
        day = _days_ago(5)
        _account(db, "PONE", PARKER, "investor360", 0)
        _account(db, "ROBIN", "Robinhood", "plaid", 1, subtype="brokerage")
        _i360(db, "PONE", day, "VTI", 10, 1000, "Large-Cap Blend")
        _plaid(db, "ROBIN", day, "VTI", 10, 1000, sec_type="etf")
        assert auto_link_accounts(db) == 0

    def test_relinked_login_gets_the_link_back(self, db, moved, client, auth_headers):
        """Removing and adding a login again gives new Plaid account ids: the link
        follows the new account, matched against the old LPL holdings (which may
        have drifted from Parker's by reinvested dividends)."""
        from api.account_links import auto_link_accounts
        _link_all(db)
        db.execute("UPDATE accounts SET is_active = 0 WHERE account_number = 'S1'")
        _account(db, "S1NEW", LPL, "plaid", 1)
        day = _days_ago(1)
        # S1's last holdings drifted from Parker's 10 / 20 shares; the new account matches S1
        db.execute("UPDATE plaid_holdings SET quantity = quantity * 1.03 WHERE account_number = 'S1'")
        _plaid(db, "S1NEW", day, "AAA", 10.3, 1200)
        _plaid(db, "S1NEW", day, "BBB", 20.6, 2200)
        _balance(db, "S1NEW", day, 3400.0)
        assert auto_link_accounts(db) == 1
        assert ("P1", "S1NEW", "auto-holdings") in _links(db)
        new_id = db.execute("SELECT id FROM accounts WHERE account_number = 'S1NEW'").fetchone()[0]
        rows = client.get(f"/api/accounts/{new_id}/balances?days=365", headers=auth_headers).json()
        assert rows[0]["current"] == 3050.0 and rows[-1]["current"] == 3400.0

    def test_owner_unlinked_pair_is_not_repointed(self, db, moved):
        from api.account_links import auto_link_accounts
        _link_all(db)
        db.execute("UPDATE account_links SET unlinked_at = CURRENT_TIMESTAMP WHERE predecessor_account_number = 'P1'")
        db.execute("UPDATE accounts SET is_active = 0 WHERE account_number = 'S1'")
        _account(db, "S1NEW", LPL, "plaid", 1)
        _plaid(db, "S1NEW", _days_ago(1), "AAA", 10, 1100)
        _plaid(db, "S1NEW", _days_ago(1), "BBB", 20, 2100)
        assert auto_link_accounts(db) == 0


class TestRepairEdges:
    def test_pair_linked_later_is_still_added(self, db, moved):
        from api.account_links import repair_cutover_snapshots
        mid = _days_ago(10)
        db.execute("INSERT INTO net_worth_snapshots (snapped_at, total, invested) VALUES (?, 5100, 5000)", (mid,))
        db.execute("INSERT INTO account_links (predecessor_account_number, successor_account_number, method) "
                   "VALUES ('P1', 'S1', 'test')")
        assert repair_cutover_snapshots(db, dates=(mid,), reason="test") == pytest.approx(3050.0)
        db.execute("INSERT INTO account_links (predecessor_account_number, successor_account_number, method) "
                   "VALUES ('P2', 'S2', 'test')")
        assert repair_cutover_snapshots(db, dates=(mid,), reason="test") == pytest.approx(1200.0)
        assert repair_cutover_snapshots(db, dates=(mid,), reason="test") == 0
        assert db.execute("SELECT invested FROM net_worth_snapshots WHERE snapped_at = ?",
                          (mid,)).fetchone()[0] == pytest.approx(5000 + 3050 + 1200)
        assert db.execute("SELECT SUM(amount) FROM net_worth_snapshot_repairs").fetchone()[0] == pytest.approx(4250)

    def test_repointed_pair_is_not_added_again(self, db, moved):
        """A pair counted through its old successor stays counted after a repoint
        to a new account that has no balance on that date."""
        from api.account_links import auto_link_accounts, repair_cutover_snapshots
        _link_all(db)
        d = moved["new"]
        db.execute("INSERT INTO net_worth_snapshots (snapped_at, total, invested) VALUES (?, 100, 0)", (d,))
        assert repair_cutover_snapshots(db, dates=(d,), reason="test") == 0
        db.execute("UPDATE accounts SET is_active = 0 WHERE account_number = 'S1'")
        _account(db, "S1NEW", LPL, "plaid", 1)
        _plaid(db, "S1NEW", _days_ago(1), "AAA", 10, 1100)
        _plaid(db, "S1NEW", _days_ago(1), "BBB", 20, 2100)
        assert auto_link_accounts(db) == 1
        assert repair_cutover_snapshots(db, dates=(d,), reason="test") == 0
        assert db.execute("SELECT invested FROM net_worth_snapshots WHERE snapped_at = ?", (d,)).fetchone()[0] == 0

    def test_today_is_left_to_the_snapshot_code(self, db, moved):
        from api.account_links import repair_cutover_snapshots
        _link_all(db)
        today = date.today().isoformat()
        db.execute("DELETE FROM account_balances WHERE account_number IN ('S1', 'S2')")
        db.execute("INSERT INTO net_worth_snapshots (snapped_at, total, invested) VALUES (?, 100, 0)", (today,))
        assert repair_cutover_snapshots(db, dates=(today,), reason="test") == 0


class TestTotalsAndFallbacks:
    def test_summary_gives_the_whole_holdings_total(self, db, moved, client, auth_headers):
        _link_all(db)
        g = client.get("/api/linked-accounts/summary", headers=auth_headers).json()["groups"][0]
        assert g["holdings_total"] == pytest.approx(1100 + 2100 + 60 + 550 + 720 + 100 + 30)
        assert "account_ids" not in g and "asset_classes" not in g

    def test_account_list_shows_the_predecessor_balance_until_the_first_own(
            self, db, moved, client, auth_headers):
        _link_all(db)
        db.execute("DELETE FROM account_balances WHERE account_number = 'S2'")
        db.commit()
        rows = {a["coinbase_uuid"]: a for a in client.get("/api/accounts", headers=auth_headers).json()}
        assert rows["pid-S2"]["current"] == 1200.0
        assert rows["pid-S1"]["current"] == 3260.0
        assert "account_number" not in rows["pid-S1"]

    def test_debug_breakdown_counts_an_active_predecessor_once(self, db, moved, client, auth_headers):
        _link_all(db)
        db.execute("UPDATE accounts SET is_active = 1 WHERE account_number = 'P1'")
        db.commit()
        detail = client.get("/api/net-worth/debug", headers=auth_headers).json()["plaid_accounts"]
        p1 = next(a for a in detail if a["name"] == "P1")
        assert "account_number" not in p1
        assert p1["bucket"] == "counted_through_successor" and p1["contributed"] == 0
