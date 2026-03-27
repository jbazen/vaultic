"""Tests for transaction splitting: single assign, multi-split, spending totals."""
import pytest
from api.database import get_db


def _seed_budget_and_transaction(group_name="TestGroup", items=None, txn_amount=-100.00):
    """Create a budget group, items, and a test transaction. Return (group_id, item_ids, txn_id)."""
    if items is None:
        items = ["ItemA", "ItemB"]
    with get_db() as conn:
        # Ensure a dummy account exists for the test transaction
        conn.execute("""
            INSERT OR IGNORE INTO accounts (id, name, type, is_active)
            VALUES (9999, 'TestAccount', 'depository', 1)
        """)

        conn.execute(
            "INSERT OR IGNORE INTO budget_groups (name, type, display_order) VALUES (?, 'expense', 0)",
            (group_name,)
        )
        group = conn.execute(
            "SELECT id FROM budget_groups WHERE name = ?", (group_name,)
        ).fetchone()
        gid = group["id"]

        item_ids = []
        for i, name in enumerate(items):
            conn.execute(
                "INSERT OR IGNORE INTO budget_items (group_id, name, display_order) VALUES (?, ?, ?)",
                (gid, name, i)
            )
            row = conn.execute(
                "SELECT id FROM budget_items WHERE group_id = ? AND name = ?", (gid, name)
            ).fetchone()
            item_ids.append(row["id"])

        # Create a fake transaction with account_id referencing the dummy account
        txn_id = f"test_txn_{abs(hash((group_name, tuple(items), txn_amount))) % 10**8}"
        conn.execute("""
            INSERT OR IGNORE INTO transactions (transaction_id, account_id, date, name, amount, pending)
            VALUES (?, 9999, '2026-03-15', 'Test Merchant', ?, 0)
        """, (txn_id, txn_amount))

    return gid, item_ids, txn_id


class TestSingleAssignViaSplits:
    def test_single_split_creates_assignment(self, client, auth_headers):
        _, item_ids, txn_id = _seed_budget_and_transaction(
            "SplitTestGroup1", ["Groceries"]
        )
        res = client.put(
            f"/api/budget/transactions/{txn_id}/splits",
            headers=auth_headers,
            json={"splits": [{"item_id": item_ids[0], "amount": 100.00}]},
        )
        assert res.status_code == 200

        # Verify it's stored as a direct assignment (not a split)
        with get_db() as conn:
            assignment = conn.execute(
                "SELECT * FROM transaction_assignments WHERE transaction_id = ?",
                (txn_id,)
            ).fetchone()
            assert assignment is not None
            assert assignment["item_id"] == item_ids[0]

            # No rows in transaction_splits
            splits = conn.execute(
                "SELECT * FROM transaction_splits WHERE transaction_id = ?",
                (txn_id,)
            ).fetchall()
            assert len(splits) == 0


class TestMultiSplit:
    def test_multi_split_stores_in_splits_table(self, client, auth_headers):
        _, item_ids, txn_id = _seed_budget_and_transaction(
            "SplitTestGroup2", ["Rent", "Utilities"], txn_amount=-150.00
        )
        res = client.put(
            f"/api/budget/transactions/{txn_id}/splits",
            headers=auth_headers,
            json={"splits": [
                {"item_id": item_ids[0], "amount": 100.00},
                {"item_id": item_ids[1], "amount": 50.00},
            ]},
        )
        assert res.status_code == 200

        with get_db() as conn:
            # No direct assignment
            assignment = conn.execute(
                "SELECT * FROM transaction_assignments WHERE transaction_id = ?",
                (txn_id,)
            ).fetchone()
            assert assignment is None

            # Two split rows
            splits = conn.execute(
                "SELECT * FROM transaction_splits WHERE transaction_id = ? ORDER BY amount",
                (txn_id,)
            ).fetchall()
            assert len(splits) == 2
            assert splits[0]["amount"] == 50.00
            assert splits[1]["amount"] == 100.00

    def test_split_amounts_must_sum_to_total(self, client, auth_headers):
        _, item_ids, txn_id = _seed_budget_and_transaction(
            "SplitTestGroup3", ["Food", "Gas"], txn_amount=-80.00
        )
        res = client.put(
            f"/api/budget/transactions/{txn_id}/splits",
            headers=auth_headers,
            json={"splits": [
                {"item_id": item_ids[0], "amount": 50.00},
                {"item_id": item_ids[1], "amount": 20.00},  # 70 != 80
            ]},
        )
        assert res.status_code == 422

    def test_empty_splits_rejected(self, client, auth_headers):
        _, _, txn_id = _seed_budget_and_transaction(
            "SplitTestGroup4", ["Empty"]
        )
        res = client.put(
            f"/api/budget/transactions/{txn_id}/splits",
            headers=auth_headers,
            json={"splits": []},
        )
        assert res.status_code == 422

    def test_split_nonexistent_transaction_returns_404(self, client, auth_headers):
        res = client.put(
            "/api/budget/transactions/nonexistent_txn_999/splits",
            headers=auth_headers,
            json={"splits": [{"item_id": 1, "amount": 50.00}]},
        )
        assert res.status_code == 404


class TestSplitSpendingTotals:
    def test_split_spending_appears_in_budget(self, client, auth_headers):
        """Verify that split amounts show up in the budget GET endpoint spending totals."""
        _, item_ids, txn_id = _seed_budget_and_transaction(
            "SplitSpendGroup", ["CatA", "CatB"], txn_amount=-200.00
        )

        # Set planned amounts
        with get_db() as conn:
            for iid in item_ids:
                conn.execute(
                    "INSERT OR REPLACE INTO budget_amounts (item_id, month, planned) VALUES (?, '2026-03', 500)",
                    (iid,)
                )

        # Split the transaction
        client.put(
            f"/api/budget/transactions/{txn_id}/splits",
            headers=auth_headers,
            json={"splits": [
                {"item_id": item_ids[0], "amount": 120.00},
                {"item_id": item_ids[1], "amount": 80.00},
            ]},
        )

        # Fetch the budget and verify spending
        res = client.get("/api/budget/2026-03", headers=auth_headers)
        assert res.status_code == 200
        data = res.json()

        # Find our group
        group = next((g for g in data["groups"] if g["name"] == "SplitSpendGroup"), None)
        assert group is not None

        items = {i["name"]: i for i in group["items"]}
        assert items["CatA"]["spent"] == pytest.approx(120.00, abs=0.02)
        assert items["CatB"]["spent"] == pytest.approx(80.00, abs=0.02)


class TestSplitMetadata:
    def test_split_with_check_number_and_notes(self, client, auth_headers):
        _, item_ids, txn_id = _seed_budget_and_transaction(
            "MetaGroup", ["MetaItem"], txn_amount=-50.00
        )
        res = client.put(
            f"/api/budget/transactions/{txn_id}/splits",
            headers=auth_headers,
            json={
                "splits": [{"item_id": item_ids[0], "amount": 50.00}],
                "check_number": "1234",
                "notes": "Monthly payment",
            },
        )
        assert res.status_code == 200

        with get_db() as conn:
            meta = conn.execute(
                "SELECT * FROM transaction_metadata WHERE transaction_id = ?",
                (txn_id,)
            ).fetchone()
            assert meta is not None
            assert meta["check_number"] == "1234"
            assert meta["notes"] == "Monthly payment"


class TestReassign:
    def test_reassign_clears_old_splits(self, client, auth_headers):
        """Reassigning via splits should clear previous splits."""
        _, item_ids, txn_id = _seed_budget_and_transaction(
            "ReassignGroup", ["OldItem", "NewItem"], txn_amount=-75.00
        )

        # First: split across both items
        client.put(
            f"/api/budget/transactions/{txn_id}/splits",
            headers=auth_headers,
            json={"splits": [
                {"item_id": item_ids[0], "amount": 50.00},
                {"item_id": item_ids[1], "amount": 25.00},
            ]},
        )

        # Then: reassign entirely to one item
        client.put(
            f"/api/budget/transactions/{txn_id}/splits",
            headers=auth_headers,
            json={"splits": [{"item_id": item_ids[1], "amount": 75.00}]},
        )

        with get_db() as conn:
            # Old splits should be gone
            splits = conn.execute(
                "SELECT * FROM transaction_splits WHERE transaction_id = ?",
                (txn_id,)
            ).fetchall()
            assert len(splits) == 0

            # Should be a single direct assignment now
            assignment = conn.execute(
                "SELECT * FROM transaction_assignments WHERE transaction_id = ?",
                (txn_id,)
            ).fetchone()
            assert assignment is not None
            assert assignment["item_id"] == item_ids[1]
