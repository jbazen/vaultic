class TestAccounts:
    def test_list_accounts_unauthenticated_returns_401(self, client):
        res = client.get("/api/accounts")
        assert res.status_code == 401

    def test_list_accounts_authenticated_returns_list(self, client, auth_headers):
        res = client.get("/api/accounts", headers=auth_headers)
        assert res.status_code == 200
        assert isinstance(res.json(), list)


class TestNetWorth:
    def test_latest_unauthenticated_returns_401(self, client):
        res = client.get("/api/net-worth/latest")
        assert res.status_code == 401

    def test_latest_authenticated_returns_data_or_message(self, client, auth_headers):
        res = client.get("/api/net-worth/latest", headers=auth_headers)
        assert res.status_code == 200

    def test_latest_includes_investable_field(self, client, auth_headers):
        """investable = total - real_estate - vehicles (liquid + invested assets only)."""
        res = client.get("/api/net-worth/latest", headers=auth_headers)
        assert res.status_code == 200
        data = res.json()
        # Either no data yet (message string) or a snapshot dict with investable
        if isinstance(data, dict) and "total" in data:
            assert "investable" in data

    def test_history_authenticated_returns_list(self, client, auth_headers):
        res = client.get("/api/net-worth/history", headers=auth_headers)
        assert res.status_code == 200
        assert isinstance(res.json(), list)

    def test_history_includes_investable_in_each_row(self, client, auth_headers):
        """Every history row should have an investable column."""
        res = client.get("/api/net-worth/history", headers=auth_headers)
        assert res.status_code == 200
        rows = res.json()
        for row in rows:
            assert "investable" in row

    def test_history_max_days_param(self, client, auth_headers):
        """days param up to 3650 accepted; over limit rejected."""
        res = client.get("/api/net-worth/history?days=3650", headers=auth_headers)
        assert res.status_code == 200
        res_over = client.get("/api/net-worth/history?days=9999", headers=auth_headers)
        assert res_over.status_code == 422


class TestManualEntries:
    def test_add_and_list_entry(self, client, auth_headers):
        res = client.post("/api/manual", headers=auth_headers, json={
            "name": "Primary Home",
            "category": "home_value",
            "value": 450000,
            "entered_at": "2026-03-13",
        })
        assert res.status_code == 200

        res = client.get("/api/manual", headers=auth_headers)
        assert res.status_code == 200
        entries = res.json()
        assert any(e["name"] == "Primary Home" for e in entries)

    def test_invalid_category_returns_400(self, client, auth_headers):
        res = client.post("/api/manual", headers=auth_headers, json={
            "name": "Test",
            "category": "invalid_category",
            "value": 1000,
        })
        assert res.status_code == 400

    def test_all_valid_categories_accepted(self, client, auth_headers):
        """All 10 supported categories should be accepted without 400."""
        categories = [
            "home_value", "car_value", "credit_score",
            "other_asset", "other_liability",
            "invested", "liquid", "real_estate", "vehicles", "crypto",
        ]
        for cat in categories:
            res = client.post("/api/manual", headers=auth_headers, json={
                "name": f"Test {cat}",
                "category": cat,
                "value": 1000.0,
            })
            assert res.status_code == 200, f"Category '{cat}' should be accepted"

    def test_list_entries_includes_holdings_field(self, client, auth_headers):
        """GET /api/manual should return a holdings array for every entry."""
        res = client.get("/api/manual", headers=auth_headers)
        assert res.status_code == 200
        for entry in res.json():
            assert "holdings" in entry
            assert isinstance(entry["holdings"], list)

    def test_delete_entry(self, client, auth_headers):
        # Add an entry
        res = client.post("/api/manual", headers=auth_headers, json={
            "name": "To Be Deleted",
            "category": "other_asset",
            "value": 500.0,
        })
        assert res.status_code == 200

        # Find its id
        entries = client.get("/api/manual", headers=auth_headers).json()
        entry = next((e for e in entries if e["name"] == "To Be Deleted"), None)
        assert entry is not None

        # Delete it
        res = client.delete(f"/api/manual/{entry['id']}", headers=auth_headers)
        assert res.status_code == 200

        # Confirm gone
        entries = client.get("/api/manual", headers=auth_headers).json()
        assert not any(e["name"] == "To Be Deleted" for e in entries)
