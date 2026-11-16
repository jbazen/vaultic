"""Logout revokes the access token by its jti, in the database (#146).

The revocation lives in revoked_tokens until the token's exp, so a restart or
deploy doesn't forget it. General-access tokens must carry exp, iat, sub and
jti (create_token sets all four).
"""
import sqlite3
import time
from contextlib import contextmanager

import pytest

from api import auth as auth_module
from api.auth import create_token, decode_token_claims, revoke_token
from api.database import get_db


def _me(client, token):
    return client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})


def _logout(client, token):
    return client.post("/api/auth/logout", headers={"Authorization": f"Bearer {token}"})


def _jti(token):
    return auth_module.jwt.decode(token, options={"verify_signature": False})["jti"]


def _revoked_row(jti):
    with get_db() as conn:
        return conn.execute(
            "SELECT jti, expires_at FROM revoked_tokens WHERE jti = ?", (jti,)
        ).fetchone()


def _encode(claims):
    return auth_module.jwt.encode(claims, auth_module.SECRET_KEY, algorithm=auth_module.ALGORITHM)


class TestLogoutRevocation:
    def test_logged_out_token_is_refused(self, client):
        token = create_token("testuser")
        assert _me(client, token).status_code == 200
        assert _logout(client, token).status_code == 200
        res = _me(client, token)
        assert res.status_code == 401
        assert res.headers.get("www-authenticate")

    def test_revocation_is_stored_by_jti_until_exp(self, client):
        token = create_token("testuser")
        assert _logout(client, token).status_code == 200
        claims = auth_module.jwt.decode(token, options={"verify_signature": False})
        row = _revoked_row(claims["jti"])
        assert row is not None and row["expires_at"] == claims["exp"]
        # Only the jti is stored, never the token itself.
        with get_db() as conn:
            assert not conn.execute(
                "SELECT 1 FROM revoked_tokens WHERE jti = ?", (token,)).fetchone()

    def test_revocation_is_read_from_the_database(self, client):
        """Nothing is kept in memory: the database row alone decides, so a
        restart (which keeps the database) keeps the revocation."""
        token = create_token("testuser")
        assert _logout(client, token).status_code == 200
        assert decode_token_claims(token) is None
        with get_db() as conn:
            conn.execute("DELETE FROM revoked_tokens WHERE jti = ?", (_jti(token),))
        assert decode_token_claims(token) is not None
        assert not hasattr(auth_module, "_token_denylist")

    def test_other_sessions_keep_working(self, client):
        mine, other = create_token("testuser"), create_token("testuser")
        assert _logout(client, mine).status_code == 200
        assert _me(client, other).status_code == 200

    def test_same_jti_in_a_reencoded_token_is_still_refused(self, client):
        """Keyed on the token's identity, not its exact string."""
        token = create_token("testuser")
        assert _logout(client, token).status_code == 200
        claims = auth_module.jwt.decode(token, options={"verify_signature": False})
        reencoded = auth_module.jwt.encode(claims, auth_module.SECRET_KEY,
                                           algorithm=auth_module.ALGORITHM,
                                           headers={"kid": "x"})
        assert reencoded != token
        assert _me(client, reencoded).status_code == 401

    def test_revoke_prunes_expired_rows(self):
        now = int(time.time())
        with get_db() as conn:
            conn.execute("INSERT INTO revoked_tokens (jti, expires_at) VALUES (?, ?)",
                         ("long-gone", now - 10))
            conn.execute("INSERT INTO revoked_tokens (jti, expires_at) VALUES (?, ?)",
                         ("still-live", now + 3600))
        revoke_token(create_token("testuser"))
        assert _revoked_row("long-gone") is None
        assert _revoked_row("still-live") is not None

    def test_revoking_twice_is_harmless(self):
        token = create_token("testuser")
        revoke_token(token)
        revoke_token(token)
        assert decode_token_claims(token) is None

    @pytest.mark.parametrize("token", [
        "not-a-jwt",
        _encode({"sub": "testuser", "exp": 9999999999, "iat": int(time.time())}),
    ])
    def test_unusable_tokens_are_ignored(self, token):
        with get_db() as conn:
            before = conn.execute("SELECT COUNT(*) FROM revoked_tokens").fetchone()[0]
        revoke_token(token)
        with get_db() as conn:
            assert conn.execute("SELECT COUNT(*) FROM revoked_tokens").fetchone()[0] == before


class TestRequiredClaims:
    @pytest.mark.parametrize("missing", ["exp", "iat", "sub", "jti"])
    def test_token_missing_a_claim_is_refused(self, client, missing):
        now = int(time.time())
        claims = {"sub": "testuser", "exp": now + 3600, "iat": now, "jti": f"no-{missing}"}
        del claims[missing]
        assert _me(client, _encode(claims)).status_code == 401

    @pytest.mark.parametrize("jti", [123, ""])
    def test_jti_must_be_a_nonempty_string(self, client, jti):
        now = int(time.time())
        token = _encode({"sub": "testuser", "exp": now + 3600, "iat": now, "jti": jti})
        assert _me(client, token).status_code == 401

    def test_create_token_has_every_required_claim(self):
        claims = auth_module.jwt.decode(create_token("testuser"),
                                        options={"verify_signature": False})
        assert {"exp", "iat", "sub", "jti"} <= set(claims)


class TestStartupCheck:
    @pytest.mark.parametrize("needle, message", [
        ("revoked_tokens", "revoked_tokens"),
        ("totp_last_step", "totp_last_step"),
    ])
    def test_startup_fails_without_auth_migration(self, monkeypatch, needle, message):
        import api.database as db

        bare = sqlite3.connect(":memory:", check_same_thread=False)
        bare.row_factory = sqlite3.Row

        @contextmanager
        def _bare_get_db():
            yield bare
            bare.commit()

        monkeypatch.setattr(db, "get_db", _bare_get_db)
        monkeypatch.setattr(db, "MIGRATIONS",
                            [m for m in db.MIGRATIONS if needle not in m])
        with pytest.raises(RuntimeError, match=message):
            db.init_db()
