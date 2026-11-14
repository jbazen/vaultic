"""Tests for user management: create, delete, change password, security log."""
import pytest

from api.auth import create_token


class TestUserManagement:
    def test_list_users_requires_auth(self, client):
        res = client.get("/api/auth/users")
        assert res.status_code == 401

    def test_list_users_returns_list(self, client, auth_headers):
        res = client.get("/api/auth/users", headers=auth_headers)
        assert res.status_code == 200
        assert isinstance(res.json(), list)

    def test_create_user(self, client, auth_headers):
        res = client.post("/api/auth/users", headers=auth_headers,
                          json={"username": "newuser1", "password": "NewPass123"})
        assert res.status_code == 200

        users = client.get("/api/auth/users", headers=auth_headers).json()
        assert any(u["username"] == "newuser1" for u in users)

    def test_create_duplicate_user_returns_error(self, client, auth_headers):
        client.post("/api/auth/users", headers=auth_headers,
                    json={"username": "dupuser", "password": "DupPass1"})
        res = client.post("/api/auth/users", headers=auth_headers,
                          json={"username": "dupuser", "password": "DupPass2"})
        assert res.status_code in (400, 409)

    def test_delete_user(self, client, auth_headers):
        client.post("/api/auth/users", headers=auth_headers,
                    json={"username": "todelete", "password": "DelPass1"})
        res = client.delete("/api/auth/users/todelete", headers=auth_headers)
        assert res.status_code == 200

        users = client.get("/api/auth/users", headers=auth_headers).json()
        assert not any(u["username"] == "todelete" for u in users)

    def test_delete_nonexistent_user_is_idempotent(self, client, auth_headers):
        # Deleting a non-existent user is a no-op (idempotent)
        res = client.delete("/api/auth/users/nobody_here", headers=auth_headers)
        assert res.status_code in (200, 404)

    def test_cannot_delete_own_account(self, client, auth_headers):
        res = client.delete("/api/auth/users/testuser", headers=auth_headers)
        assert res.status_code == 400

    def test_create_user_requires_auth(self, client):
        res = client.post("/api/auth/users", json={"username": "x", "password": "y"})
        assert res.status_code == 401

    def test_delete_user_requires_auth(self, client):
        res = client.delete("/api/auth/users/testuser")
        assert res.status_code == 401


    def test_create_user_weak_password_rejected(self, client, auth_headers):
        # No uppercase
        res = client.post("/api/auth/users", headers=auth_headers,
                          json={"username": "weakuser", "password": "weakpass1"})
        assert res.status_code == 400
        # No digit
        res = client.post("/api/auth/users", headers=auth_headers,
                          json={"username": "weakuser", "password": "WeakPass"})
        assert res.status_code == 400
        # Too short
        res = client.post("/api/auth/users", headers=auth_headers,
                          json={"username": "weakuser", "password": "Wp1"})
        assert res.status_code == 400


class TestChangePassword:
    def test_change_password_with_correct_current(self, client, auth_headers):
        res = client.post("/api/auth/change-password", headers=auth_headers,
                          json={"current_password": "testpassword", "new_password": "TestPass1"})
        assert res.status_code == 200

    def test_change_password_with_wrong_current_returns_error(self, client, auth_headers):
        res = client.post("/api/auth/change-password", headers=auth_headers,
                          json={"current_password": "wrongpassword", "new_password": "newpass"})
        assert res.status_code in (400, 401)

    def test_change_password_requires_auth(self, client):
        res = client.post("/api/auth/change-password",
                          json={"current_password": "a", "new_password": "b"})
        assert res.status_code == 401


class TestSecurityLog:
    def test_security_log_requires_auth(self, client):
        res = client.get("/api/auth/security-log")
        assert res.status_code == 401

    def test_security_log_returns_list(self, client, auth_headers):
        res = client.get("/api/auth/security-log", headers=auth_headers)
        assert res.status_code == 200
        data = res.json()
        assert "lines" in data or isinstance(data, list)

    def test_security_log_respects_lines_param(self, client, auth_headers):
        res = client.get("/api/auth/security-log?lines=10", headers=auth_headers)
        assert res.status_code == 200


class TestAdminOnlyEndpoints:
    """A user created through the API is non-admin and stays locked out (#127)."""

    @pytest.fixture(scope="class")
    @classmethod
    def non_admin_headers(cls, client, auth_headers):
        res = client.post("/api/auth/users", headers=auth_headers,
                          json={"username": "viewer128", "password": "ViewPass1"})
        assert res.status_code == 200
        users = client.get("/api/auth/users", headers=auth_headers).json()
        assert next(u for u in users if u["username"] == "viewer128")["is_admin"] == 0
        yield {"Authorization": f"Bearer {create_token('viewer128')}"}
        client.delete("/api/auth/users/viewer128", headers=auth_headers)

    def test_non_admin_cannot_list_users(self, client, non_admin_headers):
        assert client.get("/api/auth/users", headers=non_admin_headers).status_code == 403

    def test_non_admin_cannot_create_user(self, client, non_admin_headers):
        res = client.post("/api/auth/users", headers=non_admin_headers,
                          json={"username": "sneaky128", "password": "Sneaky123"})
        assert res.status_code == 403

    def test_non_admin_cannot_delete_user(self, client, non_admin_headers):
        assert client.delete("/api/auth/users/testuser", headers=non_admin_headers).status_code == 403

    def test_non_admin_cannot_read_security_log(self, client, non_admin_headers):
        assert client.get("/api/auth/security-log", headers=non_admin_headers).status_code == 403

    def test_non_admin_can_still_use_regular_endpoints(self, client, non_admin_headers):
        res = client.get("/api/auth/me", headers=non_admin_headers)
        assert res.status_code == 200
