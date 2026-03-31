"""Tests for the Budget API endpoints.

Covers the key areas most likely to regress:
  - Route ordering: /groups/reorder and /items/reorder must be matched BEFORE
    /groups/{group_id} and /items/{item_id} so the literal path segment "reorder"
    is never interpreted as an integer group_id/item_id (which causes a 422).
  - CRUD for groups and items.
  - Budget amounts (planned spending per month).
  - Drag-to-reorder persistence.
  - Carryforward: new month inherits planned amounts from the most recent prior month.
  - Auto-assign: two-pass merchant+amount / auto_rules matching.
  - CSV import: creates groups/items, populates history, seeds auto_rules.
"""
import io


# ── Helpers ───────────────────────────────────────────────────────────────────

def _create_group(client, auth_headers, name="Test Group", gtype="expense"):
    """Create a budget group and return the parsed JSON response."""
    res = client.post("/api/budget/groups", json={"name": name, "type": gtype},
                      headers=auth_headers)
    assert res.status_code == 200, res.text
    return res.json()


def _create_item(client, auth_headers, group_id, name="Test Item"):
    """Create a budget item inside a group and return the parsed JSON response."""
    res = client.post(f"/api/budget/groups/{group_id}/items",
                      json={"name": name}, headers=auth_headers)
    assert res.status_code == 200, res.text
    return res.json()


# ── Auth guard tests ───────────────────────────────────────────────────────────

class TestBudgetAuth:
    """All budget endpoints require a valid JWT."""

    def test_get_budget_requires_auth(self, client):
        res = client.get("/api/budget/2026-03")
        assert res.status_code == 401

    def test_create_group_requires_auth(self, client):
        res = client.post("/api/budget/groups", json={"name": "X", "type": "expense"})
        assert res.status_code == 401

    def test_reorder_groups_requires_auth(self, client):
        res = client.patch("/api/budget/groups/reorder", json={"ids": [1]})
        assert res.status_code == 401

    def test_reorder_items_requires_auth(self, client):
        res = client.patch("/api/budget/items/reorder", json={"ids": [1]})
        assert res.status_code == 401


# ── Route collision regression tests ──────────────────────────────────────────

class TestReorderRouteOrdering:
    """Regression tests for the 422 bug caused by route ordering.

    PATCH /groups/reorder was defined AFTER PATCH /groups/{group_id}, so FastAPI
    matched the literal string "reorder" as the group_id integer parameter and
    returned 422 Unprocessable Content.  The reorder endpoints must now be defined
    first so they win the route match before the {group_id} wildcard.
    """

    def test_reorder_groups_not_422(self, client, auth_headers):
        """Sending a valid reorder body must not return 422.

        422 would mean the router matched /groups/{group_id} (trying to parse
        "reorder" as int) instead of /groups/reorder.
        """
        g1 = _create_group(client, auth_headers, "Reorder Group A")
        g2 = _create_group(client, auth_headers, "Reorder Group B")
        res = client.patch(
            "/api/budget/groups/reorder",
            json={"ids": [g1["id"], g2["id"]]},
            headers=auth_headers,
        )
        assert res.status_code != 422, (
            "PATCH /groups/reorder matched the {group_id} wildcard route — "
            "reorder endpoint must be defined before /{group_id}"
        )
        assert res.status_code == 200

    def test_reorder_items_not_422(self, client, auth_headers):
        """Same route-order regression test for items."""
        g = _create_group(client, auth_headers, "Items Reorder Group")
        i1 = _create_item(client, auth_headers, g["id"], "Item A")
        i2 = _create_item(client, auth_headers, g["id"], "Item B")
        res = client.patch(
            "/api/budget/items/reorder",
            json={"ids": [i1["id"], i2["id"]]},
            headers=auth_headers,
        )
        assert res.status_code != 422, (
            "PATCH /items/reorder matched the {item_id} wildcard route"
        )
        assert res.status_code == 200

    def test_reorder_groups_persists_order(self, client, auth_headers):
        """After reordering, GET /{month} returns groups in the new order."""
        g1 = _create_group(client, auth_headers, "Order First")
        g2 = _create_group(client, auth_headers, "Order Second")
        # Swap them
        res = client.patch(
            "/api/budget/groups/reorder",
            json={"ids": [g2["id"], g1["id"]]},
            headers=auth_headers,
        )
        assert res.status_code == 200
        data = res.json()
        assert data.get("ok") is True


# ── Group CRUD ─────────────────────────────────────────────────────────────────

class TestBudgetGroups:
    """Create, read, update, and soft-delete budget groups."""

    def test_create_group(self, client, auth_headers):
        data = _create_group(client, auth_headers, "Housing")
        assert data["id"] > 0
        assert data["name"] == "Housing"

    def test_create_income_group(self, client, auth_headers):
        data = _create_group(client, auth_headers, "Salary", gtype="income")
        assert data["type"] == "income"

    def test_create_group_invalid_type(self, client, auth_headers):
        res = client.post("/api/budget/groups",
                          json={"name": "Bad", "type": "invalid"},
                          headers=auth_headers)
        assert res.status_code == 400

    def test_rename_group(self, client, auth_headers):
        g = _create_group(client, auth_headers, "Old Name")
        res = client.patch(f"/api/budget/groups/{g['id']}",
                           json={"name": "New Name"}, headers=auth_headers)
        assert res.status_code == 200
        assert res.json()["name"] == "New Name"

    def test_delete_group(self, client, auth_headers):
        g = _create_group(client, auth_headers, "To Delete")
        res = client.delete(f"/api/budget/groups/{g['id']}", headers=auth_headers)
        assert res.status_code == 200
        assert res.json()["ok"] is True

    def test_get_budget_returns_groups(self, client, auth_headers):
        _create_group(client, auth_headers, "Visible Group")
        res = client.get("/api/budget/2026-03", headers=auth_headers)
        assert res.status_code == 200
        data = res.json()
        assert "groups" in data
        assert isinstance(data["groups"], list)


# ── Item CRUD ──────────────────────────────────────────────────────────────────

class TestBudgetItems:
    """Create, rename, and soft-delete budget line items."""

    def setup_method(self, method):
        pass  # groups created per-test to keep them isolated

    def test_create_item(self, client, auth_headers):
        g = _create_group(client, auth_headers, "Food")
        item = _create_item(client, auth_headers, g["id"], "Groceries")
        assert item["id"] > 0
        assert item["name"] == "Groceries"

    def test_rename_item(self, client, auth_headers):
        g = _create_group(client, auth_headers, "Transport")
        item = _create_item(client, auth_headers, g["id"], "Gas")
        res = client.patch(f"/api/budget/items/{item['id']}",
                           json={"name": "Fuel"}, headers=auth_headers)
        assert res.status_code == 200
        assert res.json()["name"] == "Fuel"

    def test_delete_item(self, client, auth_headers):
        g = _create_group(client, auth_headers, "Misc")
        item = _create_item(client, auth_headers, g["id"], "To Remove")
        res = client.delete(f"/api/budget/items/{item['id']}", headers=auth_headers)
        assert res.status_code == 200
        assert res.json()["ok"] is True


# ── Budget amounts ─────────────────────────────────────────────────────────────

class TestBudgetAmounts:
    """Setting and reading planned amounts per month."""

    def test_set_and_read_planned_amount(self, client, auth_headers):
        g = _create_group(client, auth_headers, "Utilities")
        item = _create_item(client, auth_headers, g["id"], "Electric")
        res = client.put(
            f"/api/budget/items/{item['id']}/amount",
            json={"month": "2026-03", "planned": 150.0},
            headers=auth_headers,
        )
        assert res.status_code == 200

    def test_planned_amount_appears_in_budget(self, client, auth_headers):
        g = _create_group(client, auth_headers, "Insurance")
        item = _create_item(client, auth_headers, g["id"], "Car Insurance")
        client.put(
            f"/api/budget/items/{item['id']}/amount",
            json={"month": "2026-04", "planned": 200.0},
            headers=auth_headers,
        )
        res = client.get("/api/budget/2026-04", headers=auth_headers)
        assert res.status_code == 200
        groups = res.json()["groups"]
        all_items = [i for grp in groups for i in grp.get("items", [])]
        matching = [i for i in all_items if i["id"] == item["id"]]
        assert matching, "item not found in budget response"
        assert matching[0]["planned"] == 200.0


# ── Carryforward ──────────────────────────────────────────────────────────────

class TestCarryforward:
    """GET /{month} copies planned amounts from the most recent prior month
    when the target month has no amounts yet."""

    def test_carryforward_copies_prior_month(self, client, auth_headers):
        """A new month with no planned amounts inherits from the prior month."""
        g = _create_group(client, auth_headers, "CF Group")
        item = _create_item(client, auth_headers, g["id"], "CF Item")
        # Set planned amount in a base month
        client.put(f"/api/budget/items/{item['id']}/amount",
                   json={"month": "2028-01", "planned": 500.0},
                   headers=auth_headers)

        # GET a future month that has no amounts — should carryforward
        res = client.get("/api/budget/2028-02", headers=auth_headers)
        assert res.status_code == 200
        groups = res.json()["groups"]
        all_items = [i for grp in groups for i in grp.get("items", [])]
        match = [i for i in all_items if i["id"] == item["id"]]
        assert match, "item not found after carryforward"
        assert match[0]["planned"] == 500.0

    def test_carryforward_does_not_overwrite(self, client, auth_headers):
        """If the target month already has an amount, carryforward doesn't run."""
        g = _create_group(client, auth_headers, "CF No Overwrite")
        item = _create_item(client, auth_headers, g["id"], "CF Keep")
        # Set planned in two months
        client.put(f"/api/budget/items/{item['id']}/amount",
                   json={"month": "2029-01", "planned": 300.0},
                   headers=auth_headers)
        client.put(f"/api/budget/items/{item['id']}/amount",
                   json={"month": "2029-02", "planned": 750.0},
                   headers=auth_headers)

        # GET 2029-02 — carryforward should NOT run (it has amounts already)
        res = client.get("/api/budget/2029-02", headers=auth_headers)
        assert res.status_code == 200
        groups = res.json()["groups"]
        all_items = [i for grp in groups for i in grp.get("items", [])]
        match = [i for i in all_items if i["id"] == item["id"]]
        assert match[0]["planned"] == 750.0, "carryforward overwrote existing amount"


# ── Auto-assign ───────────────────────────────────────────────────────────────

def _seed_test_account(client, auth_headers):
    """Insert a minimal account row so transaction foreign keys are satisfied."""
    from tests.conftest import _test_get_db
    with _test_get_db() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO accounts (id, name, type)
            VALUES (9999, 'Test Checking', 'depository')
        """)
    return 9999


class TestAutoAssign:
    """POST /auto-assign/{month} — two-pass merchant+amount matching."""

    def test_auto_assign_no_transactions(self, client, auth_headers):
        """When no unassigned transactions exist, returns 0/0."""
        res = client.post("/api/budget/auto-assign/2030-01", headers=auth_headers)
        assert res.status_code == 200
        data = res.json()
        assert data["assigned"] == 0
        assert data["skipped"] == 0

    def test_auto_assign_pass1_merchant_amount(self, client, auth_headers):
        """Pass 1: transaction matched by (merchant, amount) from budget_history."""
        acct_id = _seed_test_account(client, auth_headers)
        g = _create_group(client, auth_headers, "AA Food")
        item = _create_item(client, auth_headers, g["id"], "AA Groceries")

        from tests.conftest import _test_get_db
        with _test_get_db() as conn:
            # Insert a Plaid transaction
            conn.execute("""
                INSERT OR IGNORE INTO transactions
                    (transaction_id, account_id, amount, date, merchant_name, pending)
                VALUES ('aa-txn-001', ?, -52.10, '2030-06-15', 'Kroger', 0)
            """, (acct_id,))
            # Insert matching budget_history
            conn.execute("""
                INSERT INTO budget_history (group_name, item_id, item_name, month, date, merchant, amount, type)
                VALUES ('Food', ?, 'Groceries', '2030-06', '2030-06-10', 'Kroger', 52.10, 'expense')
            """, (item["id"],))

        res = client.post("/api/budget/auto-assign/2030-06", headers=auth_headers)
        assert res.status_code == 200
        assert res.json()["assigned"] >= 1

    def test_auto_assign_pass2_auto_rules(self, client, auth_headers):
        """Pass 2: transaction matched by merchant via budget_auto_rules."""
        acct_id = _seed_test_account(client, auth_headers)
        g = _create_group(client, auth_headers, "AA Subscription")
        item = _create_item(client, auth_headers, g["id"], "AA Netflix")

        from tests.conftest import _test_get_db
        with _test_get_db() as conn:
            # Insert a transaction with no budget_history match
            conn.execute("""
                INSERT OR IGNORE INTO transactions
                    (transaction_id, account_id, amount, date, merchant_name, pending)
                VALUES ('aa-txn-002', ?, -15.99, '2030-07-05', 'NETFLIX INC', 0)
            """, (acct_id,))
            # Insert auto_rule for netflix
            conn.execute("""
                INSERT OR IGNORE INTO budget_auto_rules (merchant, item_id, match_count)
                VALUES ('NETFLIX INC', ?, 5)
            """, (item["id"],))

        res = client.post("/api/budget/auto-assign/2030-07", headers=auth_headers)
        assert res.status_code == 200
        assert res.json()["assigned"] >= 1

    def test_auto_assign_skips_ambiguous(self, client, auth_headers):
        """Transactions with no match in history or rules are skipped."""
        acct_id = _seed_test_account(client, auth_headers)

        from tests.conftest import _test_get_db
        with _test_get_db() as conn:
            conn.execute("""
                INSERT OR IGNORE INTO transactions
                    (transaction_id, account_id, amount, date, merchant_name, pending)
                VALUES ('aa-txn-003', ?, -99.99, '2030-08-01', 'UNKNOWN STORE XYZ', 0)
            """, (acct_id,))

        res = client.post("/api/budget/auto-assign/2030-08", headers=auth_headers)
        assert res.status_code == 200
        assert res.json()["skipped"] >= 1

    def test_auto_assign_debug_endpoint(self, client, auth_headers):
        """GET /auto-assign/{month}/debug returns a list."""
        res = client.get("/api/budget/auto-assign/2030-01/debug", headers=auth_headers)
        assert res.status_code == 200
        assert isinstance(res.json(), list)


# ── CSV import ────────────────────────────────────────────────────────────────

def _make_csv(rows):
    """Build a CSV bytes buffer from a list of dicts."""
    header = "Group,Item,Type,Date,Merchant,Amount,Note\n"
    lines = []
    for r in rows:
        lines.append(",".join([
            r.get("Group", ""), r.get("Item", ""), r.get("Type", "expense"),
            r.get("Date", "01/15/2030"), r.get("Merchant", ""),
            str(r.get("Amount", "0")), r.get("Note", ""),
        ]))
    return (header + "\n".join(lines)).encode("utf-8")


class TestCSVImport:
    """POST /import/csv — creates groups/items, populates history, seeds auto_rules."""

    def test_csv_import_basic(self, client, auth_headers):
        """A simple CSV creates groups, items, history, and auto_rules."""
        csv_data = _make_csv([
            {"Group": "Dining", "Item": "Restaurants", "Merchant": "Chipotle", "Amount": "12.50", "Date": "03/10/2030"},
            {"Group": "Dining", "Item": "Restaurants", "Merchant": "Chipotle", "Amount": "11.00", "Date": "03/17/2030"},
            {"Group": "Dining", "Item": "Coffee", "Merchant": "Starbucks", "Amount": "5.75", "Date": "03/20/2030"},
        ])
        res = client.post(
            "/api/budget/import/csv",
            files=[("files", ("test.csv", io.BytesIO(csv_data), "text/csv"))],
            headers=auth_headers,
        )
        assert res.status_code == 200
        data = res.json()
        assert data["rows_imported"] == 3
        assert data["groups_created"] >= 1
        assert data["items_created"] >= 1
        assert "2030-03" in data["months_covered"]

    def test_csv_import_seeds_auto_rules(self, client, auth_headers):
        """After CSV import, auto_rules are seeded for each merchant→item pair."""
        csv_data = _make_csv([
            {"Group": "Transport", "Item": "Gas", "Merchant": "Shell", "Amount": "45.00", "Date": "04/01/2030"},
            {"Group": "Transport", "Item": "Gas", "Merchant": "Shell", "Amount": "42.00", "Date": "04/15/2030"},
        ])
        res = client.post(
            "/api/budget/import/csv",
            files=[("files", ("gas.csv", io.BytesIO(csv_data), "text/csv"))],
            headers=auth_headers,
        )
        assert res.status_code == 200
        assert res.json()["rules_seeded"] >= 1

    def test_csv_import_skips_blank_groups(self, client, auth_headers):
        """Rows with blank Group are skipped."""
        csv_data = _make_csv([
            {"Group": "", "Item": "Ghost", "Merchant": "Nobody", "Amount": "1.00"},
            {"Group": "Valid", "Item": "Real", "Merchant": "Store", "Amount": "5.00"},
        ])
        res = client.post(
            "/api/budget/import/csv",
            files=[("files", ("blank.csv", io.BytesIO(csv_data), "text/csv"))],
            headers=auth_headers,
        )
        assert res.status_code == 200
        assert res.json()["rows_imported"] == 1

    def test_csv_import_requires_auth(self, client):
        """CSV import requires authentication."""
        csv_data = _make_csv([{"Group": "X", "Item": "Y", "Merchant": "Z", "Amount": "1"}])
        res = client.post(
            "/api/budget/import/csv",
            files=[("files", ("test.csv", io.BytesIO(csv_data), "text/csv"))],
        )
        assert res.status_code == 401


# ── Refund / credit handling ─────────────────────────────────────────────────

class TestRefundHandling:
    """Verify that credit/refund transactions (negative amounts) reduce spending
    instead of adding to it, and are displayed correctly in item detail."""

    def test_refund_reduces_spent(self, client, auth_headers):
        """A refund (negative amount) assigned to a budget item should reduce its spent total."""
        acct_id = _seed_test_account(client, auth_headers)
        g = _create_group(client, auth_headers, "Refund Test Group")
        item = _create_item(client, auth_headers, g["id"], "Refund Test Item")
        client.put(
            f"/api/budget/items/{item['id']}/amount",
            json={"month": "2031-01", "planned": 200.0},
            headers=auth_headers,
        )

        from tests.conftest import _test_get_db
        with _test_get_db() as conn:
            # Insert a debit ($50 purchase) and a credit (-$20 refund)
            conn.execute("""
                INSERT OR IGNORE INTO transactions
                    (transaction_id, account_id, amount, date, merchant_name, pending)
                VALUES ('refund-debit-001', ?, 50.00, '2031-01-10', 'Amazon', 0)
            """, (acct_id,))
            conn.execute("""
                INSERT OR IGNORE INTO transactions
                    (transaction_id, account_id, amount, date, merchant_name, pending)
                VALUES ('refund-credit-001', ?, -20.00, '2031-01-12', 'Amazon', 0)
            """, (acct_id,))
            # Assign both to the same budget item
            conn.execute("""
                INSERT OR IGNORE INTO transaction_assignments (transaction_id, item_id, status)
                VALUES ('refund-debit-001', ?, 'manual')
            """, (item["id"],))
            conn.execute("""
                INSERT OR IGNORE INTO transaction_assignments (transaction_id, item_id, status)
                VALUES ('refund-credit-001', ?, 'manual')
            """, (item["id"],))

        res = client.get("/api/budget/2031-01", headers=auth_headers)
        assert res.status_code == 200
        data = res.json()

        # Find our test group+item
        test_group = next(g for g in data["groups"] if g["name"] == "Refund Test Group")
        test_item = next(i for i in test_group["items"] if i["name"] == "Refund Test Item")

        # Spent should be 50 - 20 = 30, NOT 50 + 20 = 70
        assert test_item["spent"] == 30.0
        assert test_item["remaining"] == 170.0  # 200 planned - 30 spent

    def test_refund_item_detail_preserves_sign(self, client, auth_headers):
        """Item detail endpoint should show negative amount for refund transactions."""
        acct_id = _seed_test_account(client, auth_headers)
        g = _create_group(client, auth_headers, "Detail Refund Group")
        item = _create_item(client, auth_headers, g["id"], "Detail Refund Item")

        from tests.conftest import _test_get_db
        with _test_get_db() as conn:
            conn.execute("""
                INSERT OR IGNORE INTO transactions
                    (transaction_id, account_id, amount, date, merchant_name, pending)
                VALUES ('detail-refund-001', ?, -25.00, '2031-03-10', 'Target', 0)
            """, (acct_id,))
            conn.execute("""
                INSERT OR IGNORE INTO transaction_assignments (transaction_id, item_id, status)
                VALUES ('detail-refund-001', ?, 'manual')
            """, (item["id"],))

        res = client.get(f"/api/budget/items/{item['id']}/detail?month=2031-03",
                         headers=auth_headers)
        assert res.status_code == 200
        data = res.json()
        # The refund transaction should have a negative display_amount
        refund_txn = next(t for t in data["transactions"] if t["transaction_id"] == "detail-refund-001")
        assert refund_txn["amount"] < 0  # Preserved sign, not ABS'd

    def test_refund_only_item_shows_negative_spent(self, client, auth_headers):
        """A budget item with only refunds should have negative spent total."""
        acct_id = _seed_test_account(client, auth_headers)
        g = _create_group(client, auth_headers, "Refund Only Group")
        item = _create_item(client, auth_headers, g["id"], "Refund Only Item")
        client.put(
            f"/api/budget/items/{item['id']}/amount",
            json={"month": "2031-02", "planned": 100.0},
            headers=auth_headers,
        )

        from tests.conftest import _test_get_db
        with _test_get_db() as conn:
            conn.execute("""
                INSERT OR IGNORE INTO transactions
                    (transaction_id, account_id, amount, date, merchant_name, pending)
                VALUES ('refund-only-001', ?, -35.15, '2031-02-05', 'Amazon', 0)
            """, (acct_id,))
            conn.execute("""
                INSERT OR IGNORE INTO transaction_assignments (transaction_id, item_id, status)
                VALUES ('refund-only-001', ?, 'manual')
            """, (item["id"],))

        res = client.get("/api/budget/2031-02", headers=auth_headers)
        data = res.json()
        test_group = next(g for g in data["groups"] if g["name"] == "Refund Only Group")
        test_item = next(i for i in test_group["items"] if i["name"] == "Refund Only Item")

        # Spent should be -35.15 (credit), remaining should be 135.15
        assert test_item["spent"] == -35.15
        assert test_item["remaining"] == 135.15
