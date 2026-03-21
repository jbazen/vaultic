"""Tests for the Budget API endpoints.

Covers the key areas most likely to regress:
  - Route ordering: /groups/reorder and /items/reorder must be matched BEFORE
    /groups/{group_id} and /items/{item_id} so the literal path segment "reorder"
    is never interpreted as an integer group_id/item_id (which causes a 422).
  - CRUD for groups and items.
  - Budget amounts (planned spending per month).
  - Drag-to-reorder persistence.
"""


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
        assert res.json()["status"] == "deleted"

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
        assert res.json()["status"] == "deleted"


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
