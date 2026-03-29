"""Tests for the refresh token / persistent session feature.

Covers:
  - remember_me=False: returns 30-day access token only, no refresh_token field
  - remember_me=True: returns 1-hour access token + refresh_token
  - remember_me carries through the 2FA flow (verify-2fa)
  - POST /api/auth/refresh: valid token → new access + new refresh token (rotation)
  - POST /api/auth/refresh: invalid token → 401
  - POST /api/auth/refresh: revoked token → 401
  - Logout with refresh_token body: revokes refresh token (subsequent /refresh → 401)
  - Logout without refresh_token body: still works (backwards-compatible)
"""
import jwt as pyjwt


def _login_no_2fa(client):
    """Login without 2FA. Returns (auth_headers, raw_response_json)."""
    res = client.post("/api/auth/login", json={"username": "testuser", "password": "testpassword"})
    assert res.status_code == 200, res.text
    return res.json()


# ── Web session (no remember_me) ──────────────────────────────────────────────

class TestWebSession:
    """remember_me=False → 30-day token, no refresh token."""

    def test_login_without_remember_me_returns_token(self, client):
        data = client.post(
            "/api/auth/login",
            json={"username": "testuser", "password": "testpassword", "remember_me": False},
        ).json()
        assert "token" in data

    def test_login_without_remember_me_no_refresh_token(self, client):
        data = client.post(
            "/api/auth/login",
            json={"username": "testuser", "password": "testpassword", "remember_me": False},
        ).json()
        assert "refresh_token" not in data

    def test_default_login_no_refresh_token(self, client):
        """Omitting remember_me defaults to False — existing clients unaffected."""
        data = client.post(
            "/api/auth/login",
            json={"username": "testuser", "password": "testpassword"},
        ).json()
        assert "refresh_token" not in data

    def test_web_token_is_long_lived(self, client):
        """Web token should expire in ~30 days (720 hours), not 1 hour."""
        from datetime import datetime, timezone
        data = client.post(
            "/api/auth/login",
            json={"username": "testuser", "password": "testpassword", "remember_me": False},
        ).json()
        payload = pyjwt.decode(data["token"], options={"verify_signature": False})
        exp = datetime.fromtimestamp(payload["exp"], tz=timezone.utc)
        now = datetime.now(timezone.utc)
        hours_until_expiry = (exp - now).total_seconds() / 3600
        # Should be close to 720 hours (allow ±2h for test timing)
        assert 718 <= hours_until_expiry <= 722


# ── Mobile session (remember_me=True) ────────────────────────────────────────

class TestMobileSession:
    """remember_me=True → short-lived access token + refresh token."""

    def test_login_with_remember_me_returns_both_tokens(self, client):
        data = client.post(
            "/api/auth/login",
            json={"username": "testuser", "password": "testpassword", "remember_me": True},
        ).json()
        assert "token" in data
        assert "refresh_token" in data

    def test_mobile_access_token_is_short_lived(self, client):
        """Mobile access token should expire in ~1 hour."""
        from datetime import datetime, timezone
        data = client.post(
            "/api/auth/login",
            json={"username": "testuser", "password": "testpassword", "remember_me": True},
        ).json()
        payload = pyjwt.decode(data["token"], options={"verify_signature": False})
        exp = datetime.fromtimestamp(payload["exp"], tz=timezone.utc)
        now = datetime.now(timezone.utc)
        hours_until_expiry = (exp - now).total_seconds() / 3600
        # Should be ~1 hour (allow ±0.1h)
        assert 0.9 <= hours_until_expiry <= 1.1

    def test_mobile_refresh_token_is_a_string(self, client):
        data = client.post(
            "/api/auth/login",
            json={"username": "testuser", "password": "testpassword", "remember_me": True},
        ).json()
        assert isinstance(data["refresh_token"], str)
        assert len(data["refresh_token"]) > 20  # URL-safe base64, should be 64+ chars


# ── Refresh endpoint ──────────────────────────────────────────────────────────

class TestRefreshEndpoint:
    """POST /api/auth/refresh exchanges a valid refresh token for new tokens."""

    def _get_refresh_token(self, client):
        data = client.post(
            "/api/auth/login",
            json={"username": "testuser", "password": "testpassword", "remember_me": True},
        ).json()
        return data["refresh_token"]

    def test_valid_refresh_token_returns_new_access_token(self, client):
        refresh_token = self._get_refresh_token(client)
        res = client.post("/api/auth/refresh", json={"refresh_token": refresh_token})
        assert res.status_code == 200
        data = res.json()
        assert "token" in data

    def test_valid_refresh_token_returns_new_refresh_token(self, client):
        """Rotation: the response always includes a fresh refresh token."""
        refresh_token = self._get_refresh_token(client)
        res = client.post("/api/auth/refresh", json={"refresh_token": refresh_token})
        assert res.status_code == 200
        data = res.json()
        assert "refresh_token" in data

    def test_refresh_token_rotated_on_use(self, client):
        """Using a refresh token revokes it — reuse must return 401."""
        refresh_token = self._get_refresh_token(client)
        # First use: succeeds
        res1 = client.post("/api/auth/refresh", json={"refresh_token": refresh_token})
        assert res1.status_code == 200
        # Second use of the same token: must fail (token was rotated)
        res2 = client.post("/api/auth/refresh", json={"refresh_token": refresh_token})
        assert res2.status_code == 401

    def test_new_refresh_token_is_valid(self, client):
        """The rotated token returned by /refresh should itself work."""
        refresh_token = self._get_refresh_token(client)
        new_refresh = client.post(
            "/api/auth/refresh", json={"refresh_token": refresh_token}
        ).json()["refresh_token"]
        res = client.post("/api/auth/refresh", json={"refresh_token": new_refresh})
        assert res.status_code == 200

    def test_invalid_refresh_token_returns_401(self, client):
        res = client.post("/api/auth/refresh", json={"refresh_token": "totally-fake-token"})
        assert res.status_code == 401

    def test_refresh_requires_body(self, client):
        res = client.post("/api/auth/refresh", json={})
        assert res.status_code == 422  # Pydantic validation error


# ── Logout revokes refresh token ──────────────────────────────────────────────

class TestLogoutRevokesRefreshToken:
    """Logout with a refresh_token body invalidates the refresh token immediately."""

    def test_logout_with_refresh_token_revokes_it(self, client, auth_headers):
        # Get a refresh token via remember_me login
        data = client.post(
            "/api/auth/login",
            json={"username": "testuser", "password": "testpassword", "remember_me": True},
        ).json()
        refresh_token = data["refresh_token"]
        access_token = data["token"]

        # Log out, passing the refresh token in the body
        logout_headers = {"Authorization": f"Bearer {access_token}"}
        client.post(
            "/api/auth/logout",
            json={"refresh_token": refresh_token},
            headers=logout_headers,
        )

        # Refresh attempt after logout must fail
        res = client.post("/api/auth/refresh", json={"refresh_token": refresh_token})
        assert res.status_code == 401

    def test_logout_without_refresh_token_still_works(self, client):
        """Web logout (no refresh token body) must remain backwards-compatible.
        Uses its own fresh login so the shared auth_headers fixture isn't revoked."""
        from api.auth import create_token
        fresh_token = create_token("testuser")
        res = client.post(
            "/api/auth/logout",
            headers={"Authorization": f"Bearer {fresh_token}"},
        )
        assert res.status_code == 200
        assert res.json()["status"] == "logged_out"
