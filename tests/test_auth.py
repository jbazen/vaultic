class TestLogin:
    def test_valid_credentials_returns_token(self, client):
        res = client.post("/api/auth/login", json={"username": "testuser", "password": "testpassword"})
        assert res.status_code == 200
        assert "token" in res.json()

    def test_wrong_password_returns_401(self, client):
        res = client.post("/api/auth/login", json={"username": "testuser", "password": "wrong"})
        assert res.status_code == 401

    def test_wrong_username_returns_401(self, client):
        res = client.post("/api/auth/login", json={"username": "nobody", "password": "testpassword"})
        assert res.status_code == 401


class TestMe:
    def test_authenticated_returns_username(self, client, auth_headers):
        res = client.get("/api/auth/me", headers=auth_headers)
        assert res.status_code == 200
        assert res.json()["username"] == "testuser"

    def test_unauthenticated_returns_401(self, client):
        res = client.get("/api/auth/me")
        assert res.status_code == 401

    def test_invalid_token_returns_401(self, client):
        res = client.get("/api/auth/me", headers={"Authorization": "Bearer notavalidtoken"})
        assert res.status_code == 401


class TestHealth:
    def test_health_returns_ok(self, client):
        res = client.get("/api/health")
        assert res.status_code == 200
        assert res.json()["status"] == "ok"
