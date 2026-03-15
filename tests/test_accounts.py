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

    def test_history_authenticated_returns_list(self, client, auth_headers):
        res = client.get("/api/net-worth/history", headers=auth_headers)
        assert res.status_code == 200
        assert isinstance(res.json(), list)


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
