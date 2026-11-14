"""Deactivating a user must fully end their access (issue #99).

Covers the three gaps the issue lists:
  1. Access tokens issued before deactivation stop working.
  2. Refresh tokens are revoked on deactivation, and a deactivated user's
     refresh token is rejected even if it was left unrevoked.
  3. AUTH_USERNAME / AUTH_PASSWORD_HASH are no longer a login fallback, so a
     deactivated env user can't log in with the original password.
"""
import pytest

from api import auth as auth_module
from api.auth import (
    authenticate_user, create_refresh_token, create_token, hash_password,
    validate_refresh_token,
)
from api.database import get_db


@pytest.fixture(autouse=True)
def _reset_login_rate_limits():
    """Failed logins in these tests must not lock out later tests' logins."""
    yield
    auth_module._failed_attempts.clear()
    auth_module._failed_by_username.clear()


def _create_user(client, auth_headers, username, password):
    res = client.post("/api/auth/users", headers=auth_headers,
                      json={"username": username, "password": password})
    assert res.status_code == 200, res.text


def _login(client, username, password, remember_me=False):
    return client.post("/api/auth/login", json={
        "username": username, "password": password, "remember_me": remember_me,
    })


class TestAccessTokenAfterDeactivation:
    def test_existing_access_token_rejected_after_deactivation(self, client, auth_headers):
        _create_user(client, auth_headers, "deact_access", "DeactPass1")
        token = _login(client, "deact_access", "DeactPass1").json()["token"]
        headers = {"Authorization": f"Bearer {token}"}
        assert client.get("/api/auth/me", headers=headers).status_code == 200

        assert client.delete("/api/auth/users/deact_access", headers=auth_headers).status_code == 200

        res = client.get("/api/auth/me", headers=headers)
        assert res.status_code == 401
        # The header tells the frontend this is a real auth failure → force re-login.
        assert res.headers.get("WWW-Authenticate") == "Bearer"

    def test_token_for_user_with_no_row_rejected(self, client):
        token = create_token("ghost_user_never_created")
        res = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert res.status_code == 401

    def test_active_user_token_still_works(self, client, auth_headers):
        assert client.get("/api/auth/me", headers=auth_headers).status_code == 200


class TestRefreshTokenAfterDeactivation:
    def test_deactivation_revokes_refresh_tokens(self, client, auth_headers):
        _create_user(client, auth_headers, "deact_refresh", "DeactPass1")
        data = _login(client, "deact_refresh", "DeactPass1", remember_me=True).json()
        refresh = data["refresh_token"]

        assert client.delete("/api/auth/users/deact_refresh", headers=auth_headers).status_code == 200

        with get_db() as conn:
            unrevoked = conn.execute(
                "SELECT COUNT(*) FROM refresh_tokens WHERE username = ? AND revoked = 0",
                ("deact_refresh",),
            ).fetchone()[0]
        assert unrevoked == 0

        res = client.post("/api/auth/refresh", json={"refresh_token": refresh})
        assert res.status_code == 401

    def test_unrevoked_refresh_token_rejected_for_inactive_user(self, client, auth_headers):
        """Defence in depth: a row flipped inactive outside the API still loses refresh."""
        _create_user(client, auth_headers, "deact_direct", "DeactPass1")
        raw = create_refresh_token("deact_direct")
        assert validate_refresh_token(raw) == "deact_direct"

        with get_db() as conn:
            conn.execute("UPDATE users SET is_active = 0 WHERE username = ?", ("deact_direct",))

        assert validate_refresh_token(raw) is None

    def test_deactivation_leaves_other_users_refresh_tokens(self, client, auth_headers):
        _create_user(client, auth_headers, "deact_other", "DeactPass1")
        raw = create_refresh_token("testuser")

        assert client.delete("/api/auth/users/deact_other", headers=auth_headers).status_code == 200

        assert validate_refresh_token(raw) == "testuser"


class TestPushAfterDeactivation:
    def test_deactivation_disables_push_subscriptions(self, client, auth_headers):
        _create_user(client, auth_headers, "deact_push", "DeactPass1")
        with get_db() as conn:
            conn.execute(
                "INSERT INTO push_subscriptions (endpoint, p256dh, auth, username) VALUES (?, ?, ?, ?)",
                ("https://push.example/deact_push", "p", "a", "deact_push"),
            )
            conn.execute(
                "INSERT INTO push_subscriptions (endpoint, p256dh, auth, username) VALUES (?, ?, ?, ?)",
                ("https://push.example/testuser_keep", "p", "a", "testuser"),
            )

        assert client.delete("/api/auth/users/deact_push", headers=auth_headers).status_code == 200

        with get_db() as conn:
            rows = dict(conn.execute(
                "SELECT endpoint, is_active FROM push_subscriptions WHERE endpoint LIKE 'https://push.example/%'"
            ).fetchall())
        assert rows["https://push.example/deact_push"] == 0
        assert rows["https://push.example/testuser_keep"] == 1

        with get_db() as conn:  # don't leave rows behind for the push tests
            conn.execute("DELETE FROM push_subscriptions WHERE endpoint LIKE 'https://push.example/%'")


class TestLoginAfterDeactivation:
    def test_deactivated_user_cannot_log_in(self, client, auth_headers):
        _create_user(client, auth_headers, "deact_login", "DeactPass1")
        assert client.delete("/api/auth/users/deact_login", headers=auth_headers).status_code == 200

        assert _login(client, "deact_login", "DeactPass1").status_code == 401

    def test_bootstrap_fallback_blocked_for_deactivated_env_user(self, client, monkeypatch):
        """A deactivated AUTH_USERNAME must not log in via AUTH_PASSWORD_HASH."""
        env_hash = hash_password("BootPass1")
        with get_db() as conn:
            conn.execute(
                "INSERT INTO users (username, password_hash, is_active) VALUES (?, ?, 0)",
                ("deact_boot", env_hash),
            )
        monkeypatch.setenv("AUTH_USERNAME", "deact_boot")
        monkeypatch.setenv("AUTH_PASSWORD_HASH", env_hash)

        assert authenticate_user("deact_boot", "BootPass1") is False
        assert _login(client, "deact_boot", "BootPass1").status_code == 401

    def test_env_credentials_alone_do_not_log_in(self, monkeypatch):
        """With no users row, AUTH_USERNAME / AUTH_PASSWORD_HASH are not a login path."""
        monkeypatch.setenv("AUTH_USERNAME", "boot_no_row")
        monkeypatch.setenv("AUTH_PASSWORD_HASH", hash_password("BootPass1"))

        assert authenticate_user("boot_no_row", "BootPass1") is False
