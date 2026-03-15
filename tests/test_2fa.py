"""Tests for TOTP 2FA enrollment and login flow."""
import pyotp
import pytest
from api.database import get_db


def _get_totp_pending(username):
    with get_db() as conn:
        row = conn.execute(
            "SELECT totp_pending_secret FROM users WHERE username = ?", (username,)
        ).fetchone()
    return row["totp_pending_secret"] if row else None


def _get_totp_secret(username):
    with get_db() as conn:
        row = conn.execute(
            "SELECT totp_secret, two_fa_enabled FROM users WHERE username = ?", (username,)
        ).fetchone()
    return dict(row) if row else None


class TestTOTPSetup:
    def test_setup_returns_svg(self, client, auth_headers):
        res = client.post("/api/auth/2fa/setup", headers=auth_headers)
        assert res.status_code == 200
        assert "<svg" in res.text.lower()

    def test_setup_stores_pending_secret(self, client, auth_headers):
        client.post("/api/auth/2fa/setup", headers=auth_headers)
        secret = _get_totp_pending("testuser")
        assert secret is not None
        assert len(secret) > 10  # base32 secret

    def test_setup_requires_auth(self, client):
        res = client.post("/api/auth/2fa/setup")
        assert res.status_code == 401


class TestTOTPConfirm:
    def test_confirm_with_valid_code_activates_2fa(self, client, auth_headers):
        # Start fresh enrollment
        client.post("/api/auth/2fa/setup", headers=auth_headers)
        secret = _get_totp_pending("testuser")
        assert secret, "pending secret must be set after setup"

        code = pyotp.TOTP(secret).now()
        res = client.post("/api/auth/2fa/confirm", headers=auth_headers, json={"code": code})
        assert res.status_code == 200

        state = _get_totp_secret("testuser")
        assert state["two_fa_enabled"] == 1
        assert state["totp_secret"] == secret

    def test_confirm_with_invalid_code_returns_error(self, client, auth_headers):
        client.post("/api/auth/2fa/setup", headers=auth_headers)
        res = client.post("/api/auth/2fa/confirm", headers=auth_headers, json={"code": "000000"})
        assert res.status_code in (400, 401)

    def test_confirm_requires_auth(self, client):
        res = client.post("/api/auth/2fa/confirm", json={"code": "123456"})
        assert res.status_code == 401


class TestTOTPLogin:
    def test_login_requires_2fa_when_enabled(self, client, auth_headers):
        # Enable 2FA first
        client.post("/api/auth/2fa/setup", headers=auth_headers)
        secret = _get_totp_pending("testuser")
        code = pyotp.TOTP(secret).now()
        client.post("/api/auth/2fa/confirm", headers=auth_headers, json={"code": code})

        # Now login should return requires_2fa
        res = client.post("/api/auth/login", json={"username": "testuser", "password": "testpassword"})
        assert res.status_code == 200
        data = res.json()
        assert data.get("requires_2fa") is True
        assert data.get("username") == "testuser"
        assert "token" not in data

    def test_verify_2fa_with_valid_code_returns_token(self, client, auth_headers):
        # Ensure 2FA is enabled
        client.post("/api/auth/2fa/setup", headers=auth_headers)
        secret = _get_totp_pending("testuser")
        if secret:
            code = pyotp.TOTP(secret).now()
            client.post("/api/auth/2fa/confirm", headers=auth_headers, json={"code": code})

        state = _get_totp_secret("testuser")
        active_secret = state["totp_secret"]
        valid_code = pyotp.TOTP(active_secret).now()

        res = client.post("/api/auth/verify-2fa", json={"username": "testuser", "code": valid_code})
        assert res.status_code == 200
        assert "token" in res.json()

    def test_verify_2fa_with_invalid_code_returns_401(self, client):
        res = client.post("/api/auth/verify-2fa", json={"username": "testuser", "code": "000000"})
        assert res.status_code == 401


class TestTOTPDisable:
    def test_disable_2fa(self, client, auth_headers):
        # Make sure 2FA is on
        client.post("/api/auth/2fa/setup", headers=auth_headers)
        secret = _get_totp_pending("testuser")
        if secret:
            code = pyotp.TOTP(secret).now()
            client.post("/api/auth/2fa/confirm", headers=auth_headers, json={"code": code})

        res = client.delete("/api/auth/2fa", headers=auth_headers)
        assert res.status_code == 200

        state = _get_totp_secret("testuser")
        assert state["two_fa_enabled"] == 0

    def test_disable_2fa_requires_auth(self, client):
        res = client.delete("/api/auth/2fa")
        assert res.status_code == 401

    def test_login_returns_token_directly_when_2fa_disabled(self, client, auth_headers):
        # Disable 2FA first
        client.delete("/api/auth/2fa", headers=auth_headers)
        res = client.post("/api/auth/login", json={"username": "testuser", "password": "testpassword"})
        assert res.status_code == 200
        data = res.json()
        assert "token" in data or data.get("requires_2fa") is False
