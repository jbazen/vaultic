"""Tests for api/coinbase_sync.py: staked and held coins count toward a holding (#184)."""
import pytest

import api.coinbase_sync as cb
from tests.conftest import make_isolated_db

WALLET_SOL = "wallet-sol"
WALLET_ETH = "wallet-eth"
WALLET_BTC = "wallet-btc"
PRICES = {"SOL": 100.0, "ETH": 2000.0, "BTC": 50000.0, "DOGE": 0.1}


def _acct(uuid, currency, available, hold="0"):
    return {
        "uuid": uuid,
        "currency": currency,
        "available_balance": {"value": available, "currency": currency},
        "hold": {"value": hold, "currency": currency},
    }


def _staked(asset, amount, uuid=None):
    return {
        "asset": asset,
        "account_uuid": uuid or f"staked-{asset.lower()}",
        "account_type": "ACCOUNT_TYPE_STAKED_FUNDS",
        "total_balance_crypto": amount,
        "available_to_trade_crypto": 0,
    }


def _wallet_pos(asset, amount, uuid):
    return {
        "asset": asset,
        "account_uuid": uuid,
        "account_type": "ACCOUNT_TYPE_WALLET",
        "total_balance_crypto": amount,
        "available_to_trade_crypto": amount,
    }


@pytest.fixture
def db(monkeypatch):
    conn, get_db = make_isolated_db()
    monkeypatch.setattr(cb, "get_db", get_db)
    monkeypatch.setenv("COINBASE_API_KEY_NAME", "test-key")
    monkeypatch.setenv("COINBASE_API_KEY_PRIVATE", "test-private")
    monkeypatch.setattr(cb, "_get_usd_price", lambda c: PRICES.get(c, 0.0))
    yield conn
    conn.close()


def _mock_api(monkeypatch, accounts, portfolios):
    """portfolios: {uuid: (spot_positions, deleted)}"""
    def fake_get(_key, _pk, path):
        if path == "/api/v3/brokerage/accounts":
            return {"accounts": accounts}
        if path == "/api/v3/brokerage/portfolios":
            return {"portfolios": [
                {"uuid": u, "name": u, "deleted": deleted}
                for u, (_pos, deleted) in portfolios.items()
            ]}
        uuid = path.rsplit("/", 1)[-1]
        if uuid in portfolios:
            return {"breakdown": {"spot_positions": portfolios[uuid][0]}}
        raise AssertionError(f"unexpected path {path}")
    monkeypatch.setattr(cb, "_coinbase_get", fake_get)


def _latest(conn, currency):
    return conn.execute(
        "SELECT a.plaid_account_id, b.native_balance, b.current, b.unit_price "
        "FROM account_balances b JOIN accounts a ON a.id = b.account_id "
        "WHERE b.account_number = ?", (f"coinbase{currency}",)
    ).fetchone()


def test_mixed_available_and_staked_sums_into_wallet_account(db, monkeypatch):
    _mock_api(monkeypatch,
              [_acct(WALLET_SOL, "SOL", "5.874685617")],
              {"p1": ([_wallet_pos("SOL", "5.874686", WALLET_SOL), _staked("SOL", "17")], False)})

    result = cb.sync_coinbase()

    row = _latest(db, "SOL")
    assert row["plaid_account_id"] == WALLET_SOL
    assert row["native_balance"] == pytest.approx(22.874685617)
    assert row["current"] == pytest.approx(2287.47)
    assert result["synced"] == 1
    # one account per currency: no separate staked account row
    assert db.execute("SELECT COUNT(*) FROM accounts WHERE account_number = 'coinbaseSOL'").fetchone()[0] == 1


def test_staked_only_balance_is_snapshotted_on_wallet_account(db, monkeypatch):
    """ETH with zero available but staked coins gets today's snapshot (no stale day)."""
    _mock_api(monkeypatch,
              [_acct(WALLET_ETH, "ETH", "0")],
              {"p1": ([_staked("ETH", "1.0642759")], False)})

    cb.sync_coinbase()

    row = _latest(db, "ETH")
    assert row["plaid_account_id"] == WALLET_ETH
    assert row["native_balance"] == pytest.approx(1.0642759)
    assert row["current"] == pytest.approx(2128.55)


def test_hold_counts_toward_balance(db, monkeypatch):
    _mock_api(monkeypatch, [_acct(WALLET_BTC, "BTC", "0.1", hold="0.02")], {"p1": ([], False)})

    cb.sync_coinbase()

    assert _latest(db, "BTC")["native_balance"] == pytest.approx(0.12)


def test_zero_total_is_skipped(db, monkeypatch):
    _mock_api(monkeypatch,
              [_acct(WALLET_ETH, "ETH", "0"), _acct("wallet-doge", "DOGE", "0.01")],
              {"p1": ([], False)})

    result = cb.sync_coinbase()

    assert result["synced"] == 0
    assert db.execute("SELECT COUNT(*) FROM account_balances WHERE account_number LIKE 'coinbase%'").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM accounts WHERE source = 'coinbase'").fetchone()[0] == 0


def test_staked_without_wallet_account_uses_staked_uuid(db, monkeypatch):
    _mock_api(monkeypatch, [], {"p1": ([_staked("SOL", "3", uuid="staked-only-sol")], False)})

    cb.sync_coinbase()

    row = _latest(db, "SOL")
    assert row["plaid_account_id"] == "staked-only-sol"
    assert row["native_balance"] == pytest.approx(3)


def test_staked_summed_across_portfolios_and_deleted_portfolio_ignored(db, monkeypatch):
    _mock_api(monkeypatch,
              [_acct(WALLET_SOL, "SOL", "1")],
              {"p1": ([_staked("SOL", "2", uuid="s1")], False),
               "p2": ([_staked("SOL", "4", uuid="s2")], False),
               "gone": ([_staked("SOL", "100", uuid="s3")], True)})

    cb.sync_coinbase()

    assert _latest(db, "SOL")["native_balance"] == pytest.approx(7)


def test_breakdown_failure_fails_sync_without_partial_snapshot(db, monkeypatch):
    """A failed staked lookup must not write a too-low snapshot."""
    def fake_get(_key, _pk, path):
        if path == "/api/v3/brokerage/accounts":
            return {"accounts": [_acct(WALLET_SOL, "SOL", "5")]}
        raise RuntimeError("portfolio API down")
    monkeypatch.setattr(cb, "_coinbase_get", fake_get)

    with pytest.raises(RuntimeError):
        cb.sync_coinbase()

    assert db.execute("SELECT COUNT(*) FROM account_balances WHERE account_number LIKE 'coinbase%'").fetchone()[0] == 0


def test_existing_account_keeps_its_row_and_history(db, monkeypatch):
    """Resync after staking updates the same account row and today's snapshot."""
    _mock_api(monkeypatch, [_acct(WALLET_SOL, "SOL", "22")], {"p1": ([], False)})
    cb.sync_coinbase()
    acct_id = db.execute("SELECT id FROM accounts WHERE plaid_account_id = ?", (WALLET_SOL,)).fetchone()[0]

    _mock_api(monkeypatch, [_acct(WALLET_SOL, "SOL", "5")], {"p1": ([_staked("SOL", "17")], False)})
    cb.sync_coinbase()

    assert db.execute("SELECT COUNT(*) FROM accounts WHERE source = 'coinbase'").fetchone()[0] == 1
    rows = db.execute("SELECT account_id, native_balance FROM account_balances WHERE account_number LIKE 'coinbase%'").fetchall()
    assert len(rows) == 1
    assert rows[0]["account_id"] == acct_id
    assert rows[0]["native_balance"] == pytest.approx(22)
