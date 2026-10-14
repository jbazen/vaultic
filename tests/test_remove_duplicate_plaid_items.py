"""Tests for the one-time duplicate Plaid item cleanup script (issue #76)."""
import pytest

import api.database as db_module
from scripts.remove_duplicate_plaid_items import apply_plan, build_plan

KEEP, DUP = 9001, 9002


def _seed(conn, dup_mask="1941", user_status="pending_review"):
    conn.execute("DELETE FROM transaction_assignments WHERE transaction_id LIKE 'dd76-%'")
    conn.execute("DELETE FROM transactions WHERE transaction_id LIKE 'dd76-%'")
    conn.execute("DELETE FROM account_balances WHERE account_id BETWEEN 9100 AND 9299")
    conn.execute("DELETE FROM accounts WHERE id BETWEEN 9100 AND 9299")
    conn.execute("DELETE FROM plaid_items WHERE id IN (?, ?)", (KEEP, DUP))
    conn.execute("DELETE FROM net_worth_snapshots WHERE snapped_at >= '2099-01-01'")
    for item_id, created in ((KEEP, "2099-01-01 00:00:00"), (DUP, "2099-01-02 00:00:00")):
        conn.execute(
            "INSERT INTO plaid_items (id, item_id, institution_id, institution_name, access_token_enc, created_at)"
            " VALUES (?, ?, 'ins_56', 'Chase', 'enc', ?)", (item_id, f"item-{item_id}", created))
    accounts = [
        (9101, KEEP, "CHECKING", "2232", "depository", "checking", "2099-01-01 00:00:00"),
        (9102, KEEP, "CREDIT CARD", "1941", "credit", "credit card", "2099-01-01 00:00:00"),
        (9201, DUP, "CHECKING", "2232", "depository", "checking", "2099-01-02 00:00:00"),
        (9202, DUP, "CREDIT CARD", dup_mask, "credit", "credit card", "2099-01-02 00:00:00"),
    ]
    for aid, item, name, mask, typ, sub, created in accounts:
        conn.execute(
            "INSERT INTO accounts (id, plaid_account_id, plaid_item_id, name, mask, type, subtype, account_number, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (aid, f"pa-{aid}", item, name, mask, typ, sub, f"pa-{aid}", created))
    for aid in (9201, 9202):
        bal = 1000.0 if aid == 9201 else 200.0
        conn.execute(
            "INSERT INTO account_balances (account_id, current, snapped_at, account_number) VALUES (?, ?, '2099-01-02', ?)",
            (aid, bal, f"pa-{aid}"))
        conn.execute(
            "INSERT INTO transactions (transaction_id, account_id, amount, date, name, account_number)"
            " VALUES (?, ?, 5.0, '2099-01-02', 'Starbucks', ?)", (f"dd76-{aid}", aid, f"pa-{aid}"))
    conn.execute(
        "INSERT INTO transactions (transaction_id, account_id, amount, date, name, account_number)"
        " VALUES ('dd76-keep', 9102, 5.0, '2099-01-02', 'Starbucks', 'pa-9102')")
    conn.execute("INSERT INTO transaction_assignments (transaction_id, status) VALUES ('dd76-9202', ?)", (user_status,))
    conn.execute("INSERT INTO transaction_assignments (transaction_id, status) VALUES ('dd76-keep', 'manual')")
    for day, total, liquid, liab in (("2099-01-01", 5000, 3000, 500), ("2099-01-02", 5800, 4000, 700)):
        conn.execute(
            "INSERT INTO net_worth_snapshots (snapped_at, total, liquid, liabilities) VALUES (?, ?, ?, ?)",
            (day, total, liquid, liab))


def test_removes_duplicates_and_keeps_original():
    with db_module.get_db() as conn:
        _seed(conn)
        apply_plan(conn, build_plan(conn, KEEP, [DUP]))
        assert conn.execute("SELECT COUNT(*) FROM plaid_items WHERE id = ?", (DUP,)).fetchone()[0] == 0
        assert [r[0] for r in conn.execute("SELECT id FROM accounts WHERE id BETWEEN 9100 AND 9299 ORDER BY id")] == [9101, 9102]
        assert [r[0] for r in conn.execute("SELECT transaction_id FROM transactions WHERE transaction_id LIKE 'dd76-%'")] == ["dd76-keep"]
        assert [r[0] for r in conn.execute("SELECT transaction_id FROM transaction_assignments WHERE transaction_id LIKE 'dd76-%'")] == ["dd76-keep"]
        assert conn.execute("SELECT COUNT(*) FROM account_balances WHERE account_id BETWEEN 9100 AND 9299").fetchone()[0] == 0


def test_corrects_net_worth_only_from_duplicate_date():
    with db_module.get_db() as conn:
        _seed(conn)
        apply_plan(conn, build_plan(conn, KEEP, [DUP]))
        snaps = {r["snapped_at"]: r for r in conn.execute(
            "SELECT * FROM net_worth_snapshots WHERE snapped_at >= '2099-01-01'")}
        assert (snaps["2099-01-01"]["total"], snaps["2099-01-01"]["liquid"]) == (5000, 3000)
        # 2099-01-02: liquid -1000 (dup checking), liabilities -200 (dup card), total -800
        assert snaps["2099-01-02"]["liquid"] == 3000
        assert snaps["2099-01-02"]["liabilities"] == 500
        assert snaps["2099-01-02"]["total"] == 5000


def test_refuses_when_duplicate_account_has_no_match():
    with db_module.get_db() as conn:
        _seed(conn, dup_mask="0000")
        with pytest.raises(ValueError, match="no match"):
            build_plan(conn, KEEP, [DUP])


def test_refuses_when_user_work_on_duplicates():
    with db_module.get_db() as conn:
        _seed(conn, user_status="manual")
        with pytest.raises(ValueError, match="aborting"):
            build_plan(conn, KEEP, [DUP])


def test_refuses_different_institution():
    with db_module.get_db() as conn:
        _seed(conn)
        conn.execute("UPDATE plaid_items SET institution_id = 'ins_other' WHERE id = ?", (DUP,))
        with pytest.raises(ValueError, match="different institution"):
            build_plan(conn, KEEP, [DUP])


def test_refuses_keep_in_remove_list():
    with db_module.get_db() as conn:
        _seed(conn)
        with pytest.raises(ValueError, match="cannot also be removed"):
            build_plan(conn, KEEP, [KEEP, DUP])
