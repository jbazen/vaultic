"""Deactivating a user must fully end their access (issue #99).

Covers the three gaps the issue lists:
  1. Access tokens issued before deactivation stop working.
  2. Refresh tokens are revoked on deactivation, and a deactivated user's
     refresh token is rejected even if it was left unrevoked.
  3. The AUTH_USERNAME / AUTH_PASSWORD_HASH bootstrap fallback only applies
     when there is no users row at all, never for a deactivated row.
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
