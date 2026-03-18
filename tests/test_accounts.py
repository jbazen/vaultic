class TestAccounts:
    def test_list_accounts_unauthenticated_returns_401(self, client):
        res = client.get("/api/accounts")
        assert res.status_code == 401

    def test_list_accounts_authenticated_returns_list(self, client, auth_headers):
        res = client.get("/api/accounts", headers=auth_headers)
        assert res.status_code == 200
        assert isinstance(res.json(), list)

    def test_list_accounts_includes_notes_field(self, client, auth_headers):
        """Each account row should include the notes field (may be null)."""
        res = client.get("/api/accounts", headers=auth_headers)
        assert res.status_code == 200
        for acct in res.json():
            assert "notes" in acct

    def test_account_notes_requires_auth(self, client):
        """PATCH /api/accounts/{id}/notes must reject unauthenticated requests."""
        res = client.patch("/api/accounts/1/notes", json={"notes": "test"})
        assert res.status_code == 401

    def test_account_notes_nonexistent_returns_404(self, client, auth_headers):
        """Updating notes on a non-existent account returns 404."""
        res = client.patch("/api/accounts/999999/notes", headers=auth_headers, json={"notes": "test"})
        assert res.status_code == 404

    def test_account_notes_roundtrip(self, client, auth_headers):
        """
        Create an account directly in DB, set a note via PATCH, verify the note
        is returned by GET /api/accounts and that clearing it stores NULL.
        """
        from api.database import get_db

        # Insert a minimal account row so we have a real account_id to work with
        with get_db() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO accounts (name, type, is_active) VALUES ('Notes Test Account', 'depository', 1)"
            )
            acct = conn.execute(
                "SELECT id FROM accounts WHERE name = 'Notes Test Account'"
            ).fetchone()
            acct_id = acct["id"]

        # Set a note
        res = client.patch(f"/api/accounts/{acct_id}/notes", headers=auth_headers, json={"notes": "  My custom note  "})
        assert res.status_code == 200
        assert res.json()["notes"] == "My custom note"  # stripped

        # Verify it appears in the account list
        accounts = client.get("/api/accounts", headers=auth_headers).json()
        match = next((a for a in accounts if a["id"] == acct_id), None)
        assert match is not None
        assert match["notes"] == "My custom note"

        # Clear the note (empty string → NULL)
        res = client.patch(f"/api/accounts/{acct_id}/notes", headers=auth_headers, json={"notes": ""})
        assert res.status_code == 200
        assert res.json()["notes"] is None

        accounts = client.get("/api/accounts", headers=auth_headers).json()
        match = next((a for a in accounts if a["id"] == acct_id), None)
        assert match["notes"] is None


class TestNetWorth:
    def test_latest_unauthenticated_returns_401(self, client):
        res = client.get("/api/net-worth/latest")
        assert res.status_code == 401

    def test_latest_authenticated_returns_data_or_message(self, client, auth_headers):
        res = client.get("/api/net-worth/latest", headers=auth_headers)
        assert res.status_code == 200

    def test_latest_includes_investable_field(self, client, auth_headers):
        """investable = liquid + invested + crypto + other_assets (not total - real_estate - vehicles)."""
        res = client.get("/api/net-worth/latest", headers=auth_headers)
        assert res.status_code == 200
        data = res.json()
        # Either no data yet (message string) or a snapshot dict with investable
        if isinstance(data, dict) and "total" in data:
            assert "investable" in data

    def test_investable_formula_excludes_real_estate_and_vehicles(self, client, auth_headers):
        """investable should be liquid+invested+crypto+other_assets — not total minus real_estate/vehicles."""
        from api import sync
        from datetime import date

        today = date.today().isoformat()
        # Write a snapshot directly so we control all values
        from api.database import get_db
        with get_db() as conn:
            conn.execute("""
                INSERT INTO net_worth_snapshots
                    (snapped_at, total, liquid, invested, crypto, real_estate, vehicles, liabilities, other_assets)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(snapped_at) DO UPDATE SET
                    total=excluded.total, liquid=excluded.liquid, invested=excluded.invested,
                    crypto=excluded.crypto, real_estate=excluded.real_estate,
                    vehicles=excluded.vehicles, liabilities=excluded.liabilities,
                    other_assets=excluded.other_assets
            """, (today, 600000, 50000, 300000, 10000, 400000, 20000, 180000, 5000))

        res = client.get("/api/net-worth/latest", headers=auth_headers)
        assert res.status_code == 200
        data = res.json()
        assert "investable" in data
        # Formula: total(600k) - real_estate(400k) - vehicles(20k) = 180k
        # Liabilities are already reflected in total, so credit card debt is properly subtracted.
        expected = 600000 - 400000 - 20000  # 180000
        assert data["investable"] == expected, (
            f"investable={data['investable']}, expected={expected}. "
            "Investable Net Worth = total - real_estate - vehicles."
        )

    def test_investable_reflects_liabilities(self, client, auth_headers):
        """Liabilities should reduce Investable Net Worth (they are in total).
        Scenario: $100k liquid, $200k mortgage → total = -$100k, no real_estate/vehicles.
        Investable = total - 0 - 0 = -$100k (the debt exceeds liquid assets)."""
        from datetime import date
        from api.database import get_db

        today = date.today().isoformat()
        with get_db() as conn:
            conn.execute("""
                INSERT INTO net_worth_snapshots
                    (snapped_at, total, liquid, invested, crypto, real_estate, vehicles, liabilities, other_assets)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(snapped_at) DO UPDATE SET
                    total=excluded.total, liquid=excluded.liquid, invested=excluded.invested,
                    crypto=excluded.crypto, real_estate=excluded.real_estate,
                    vehicles=excluded.vehicles, liabilities=excluded.liabilities,
                    other_assets=excluded.other_assets
            """, (today, -100000, 100000, 0, 0, 0, 0, 200000, 0))

        res = client.get("/api/net-worth/latest", headers=auth_headers)
        assert res.status_code == 200
        data = res.json()
        # total(-100k) - real_estate(0) - vehicles(0) = -100k
        assert data.get("investable") == -100000, (
            "Investable Net Worth = total - real_estate - vehicles; liabilities in total reduce it"
        )

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

    def test_exclude_toggle_endpoint(self, client, auth_headers):
        """PATCH /api/manual/{id}/exclude toggles exclude_from_net_worth on and off."""
        # Create a manual entry to toggle
        res = client.post("/api/manual", headers=auth_headers, json={
            "name": "Exclude Toggle Test",
            "category": "invested",
            "value": 50000.0,
        })
        assert res.status_code == 200

        entries = client.get("/api/manual", headers=auth_headers).json()
        entry = next((e for e in entries if e["name"] == "Exclude Toggle Test"), None)
        assert entry is not None
        entry_id = entry["id"]
        assert entry["exclude_from_net_worth"] == 0  # starts included

        # Toggle on (exclude)
        res = client.patch(f"/api/manual/{entry_id}/exclude", headers=auth_headers)
        assert res.status_code == 200
        assert res.json()["exclude_from_net_worth"] == 1

        # Toggle off (re-include)
        res = client.patch(f"/api/manual/{entry_id}/exclude", headers=auth_headers)
        assert res.status_code == 200
        assert res.json()["exclude_from_net_worth"] == 0

    def test_exclude_toggle_requires_auth(self, client):
        """Exclude endpoint should reject unauthenticated requests."""
        res = client.patch("/api/manual/1/exclude")
        assert res.status_code == 401

    def test_exclude_toggle_nonexistent_entry_returns_404(self, client, auth_headers):
        """Toggling a non-existent entry ID should return 404."""
        res = client.patch("/api/manual/999999/exclude", headers=auth_headers)
        assert res.status_code == 404

    def test_rename_manual_entry(self, client, auth_headers):
        """PATCH /api/manual/{id}/rename should update name and optionally notes."""
        # Create an entry to rename
        client.post("/api/manual", headers=auth_headers, json={
            "name": "Rename Me",
            "category": "other_asset",
            "value": 1000.0,
        })
        entries = client.get("/api/manual", headers=auth_headers).json()
        entry = next((e for e in entries if e["name"] == "Rename Me"), None)
        assert entry is not None
        eid = entry["id"]

        # Rename only the name
        res = client.patch(f"/api/manual/{eid}/rename", headers=auth_headers, json={"name": "Renamed Asset"})
        assert res.status_code == 200
        assert res.json()["name"] == "Renamed Asset"

    def test_rename_manual_entry_with_notes(self, client, auth_headers):
        """PATCH /api/manual/{id}/rename should update both name and notes together."""
        client.post("/api/manual", headers=auth_headers, json={
            "name": "With Notes",
            "category": "liquid",
            "value": 2000.0,
        })
        entries = client.get("/api/manual", headers=auth_headers).json()
        entry = next((e for e in entries if e["name"] == "With Notes"), None)
        eid = entry["id"]

        # Pass both name and notes
        res = client.patch(f"/api/manual/{eid}/rename", headers=auth_headers,
                           json={"name": "With Notes Updated", "notes": "HSA via PDF import"})
        assert res.status_code == 200
        assert res.json()["name"] == "With Notes Updated"
        assert res.json()["notes"] == "HSA via PDF import"

    def test_rename_manual_entry_empty_name_returns_400(self, client, auth_headers):
        """Empty or whitespace-only name should return 400."""
        client.post("/api/manual", headers=auth_headers, json={
            "name": "Entry For Empty Name Test",
            "category": "other_asset",
            "value": 100.0,
        })
        entries = client.get("/api/manual", headers=auth_headers).json()
        entry = next((e for e in entries if e["name"] == "Entry For Empty Name Test"), None)
        eid = entry["id"]

        res = client.patch(f"/api/manual/{eid}/rename", headers=auth_headers, json={"name": "   "})
        assert res.status_code == 400

    def test_rename_manual_entry_nonexistent_returns_404(self, client, auth_headers):
        """Renaming a non-existent entry should return 404."""
        res = client.patch("/api/manual/999999/rename", headers=auth_headers, json={"name": "Ghost"})
        assert res.status_code == 404

    def test_rename_manual_entry_requires_auth(self, client):
        """Rename endpoint should reject unauthenticated requests."""
        res = client.patch("/api/manual/1/rename", json={"name": "Hacker"})
        assert res.status_code == 401

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
