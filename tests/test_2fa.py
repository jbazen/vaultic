"""Tests for TOTP 2FA enrollment and login flow."""
import pyotp
import pytest
from api.auth import decode_2fa_pending_token
from api.database import get_db
from api.encryption import decrypt


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


def _enable_2fa(client, auth_headers):
    """Helper: set up and confirm 2FA, return the plaintext TOTP secret."""
    client.post("/api/auth/2fa/setup", headers=auth_headers)
    encrypted = _get_totp_pending("testuser")
    assert encrypted, "pending secret must be set after setup"
    plain_secret = decrypt(encrypted)
    code = pyotp.TOTP(plain_secret).now()
    client.post("/api/auth/2fa/confirm", headers=auth_headers, json={"code": code})
    return plain_secret


class TestTOTPSetup:
    def test_setup_returns_svg(self, client, auth_headers):
        res = client.post("/api/auth/2fa/setup", headers=auth_headers)
        assert res.status_code == 200
        assert "<svg" in res.text.lower()

    def test_setup_stores_pending_secret_encrypted(self, client, auth_headers):
        client.post("/api/auth/2fa/setup", headers=auth_headers)
        encrypted = _get_totp_pending("testuser")
        assert encrypted is not None
        # Encrypted Fernet tokens are much longer than raw base32 secrets
        assert len(encrypted) > 50
        # Can be decrypted back to a valid base32 secret
        plain = decrypt(encrypted)
        assert len(plain) > 10

    def test_setup_requires_auth(self, client):
        res = client.post("/api/auth/2fa/setup")
        assert res.status_code == 401


class TestTOTPConfirm:
    def test_confirm_with_valid_code_activates_2fa(self, client, auth_headers):
        client.post("/api/auth/2fa/setup", headers=auth_headers)
        encrypted = _get_totp_pending("testuser")
        plain_secret = decrypt(encrypted)

        code = pyotp.TOTP(plain_secret).now()
        res = client.post("/api/auth/2fa/confirm", headers=auth_headers, json={"code": code})
        assert res.status_code == 200

        state = _get_totp_secret("testuser")
        assert state["two_fa_enabled"] == 1
        # totp_secret is stored encrypted, decrypts to original secret
        assert decrypt(state["totp_secret"]) == plain_secret

    def test_confirm_with_invalid_code_returns_error(self, client, auth_headers):
        client.post("/api/auth/2fa/setup", headers=auth_headers)
        res = client.post("/api/auth/2fa/confirm", headers=auth_headers, json={"code": "000000"})
        assert res.status_code in (400, 401)

    def test_confirm_requires_auth(self, client):
        res = client.post("/api/auth/2fa/confirm", json={"code": "123456"})
        assert res.status_code == 401


class TestTOTPLogin:
    def test_login_requires_2fa_when_enabled(self, client, auth_headers):
        _enable_2fa(client, auth_headers)

        # Login should return requires_2fa with a pending_token (not raw username)
        res = client.post("/api/auth/login", json={"username": "testuser", "password": "testpassword"})
        assert res.status_code == 200
        data = res.json()
        assert data.get("requires_2fa") is True
        assert "pending_token" in data
        assert "username" not in data  # No longer exposes raw username
        assert "token" not in data

    def test_pending_token_contains_correct_username(self, client, auth_headers):
        _enable_2fa(client, auth_headers)

        res = client.post("/api/auth/login", json={"username": "testuser", "password": "testpassword"})
        pending_token = res.json()["pending_token"]
        username = decode_2fa_pending_token(pending_token)
        assert username == "testuser"

    def test_verify_2fa_with_valid_code_returns_token(self, client, auth_headers):
        plain_secret = _enable_2fa(client, auth_headers)

        # Get pending token via login
        res = client.post("/api/auth/login", json={"username": "testuser", "password": "testpassword"})
        pending_token = res.json()["pending_token"]

        valid_code = pyotp.TOTP(plain_secret).now()
        res = client.post("/api/auth/verify-2fa", json={"pending_token": pending_token, "code": valid_code})
        assert res.status_code == 200
        assert "token" in res.json()

    def test_verify_2fa_with_invalid_code_returns_401(self, client, auth_headers):
        _enable_2fa(client, auth_headers)

        res = client.post("/api/auth/login", json={"username": "testuser", "password": "testpassword"})
        pending_token = res.json()["pending_token"]

        res = client.post("/api/auth/verify-2fa", json={"pending_token": pending_token, "code": "000000"})
        assert res.status_code == 401

    def test_verify_2fa_without_pending_token_returns_401(self, client):
        """C1 fix: cannot call verify-2fa with a fabricated/missing token."""
        res = client.post("/api/auth/verify-2fa", json={"pending_token": "bogus.token.here", "code": "123456"})
        assert res.status_code == 401

    def test_verify_2fa_with_regular_jwt_returns_401(self, client, auth_headers):
        """C1 fix: a regular auth JWT cannot be used as a 2FA pending token."""
        from api.auth import create_token
        regular_token = create_token("testuser")
        res = client.post("/api/auth/verify-2fa", json={"pending_token": regular_token, "code": "123456"})
        assert res.status_code == 401


class TestTOTPDisable:
    def test_disable_2fa_requires_password(self, client, auth_headers):
        _enable_2fa(client, auth_headers)

        res = client.post("/api/auth/2fa/disable", headers=auth_headers,
                          json={"password": "testpassword"})
        assert res.status_code == 200

        state = _get_totp_secret("testuser")
        assert state["two_fa_enabled"] == 0

    def test_disable_2fa_wrong_password_returns_401(self, client, auth_headers):
        _enable_2fa(client, auth_headers)

        res = client.post("/api/auth/2fa/disable", headers=auth_headers,
                          json={"password": "wrongpassword"})
        assert res.status_code == 401

    def test_disable_2fa_requires_auth(self, client):
        res = client.post("/api/auth/2fa/disable", json={"password": "test"})
        assert res.status_code == 401

    def test_login_returns_token_directly_when_2fa_disabled(self, client, auth_headers):
        # Disable 2FA first
        client.post("/api/auth/2fa/disable", headers=auth_headers,
                    json={"password": "testpassword"})
        res = client.post("/api/auth/login", json={"username": "testuser", "password": "testpassword"})
        assert res.status_code == 200
        data = res.json()
        assert "token" in data or data.get("requires_2fa") is False
