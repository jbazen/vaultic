"""Deactivating a user must fully end their access (issue #99).

Covers the three gaps the issue lists:
  1. Access tokens issued before deactivation stop working.
  2. Refresh tokens are revoked on deactivation, and a deactivated user's
     refresh token is rejected even if it was left unrevoked.
  3. AUTH_USERNAME / AUTH_PASSWORD_HASH are no longer a login fallback, so a
     deactivated env user can't log in with the original password.
"""
import time

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


class TestReactivationDoesNotReviveTokens:
    """Issue #126: flipping a deactivated row back to is_active = 1 must not
    bring back access tokens issued before the deactivation."""

    def test_old_token_rejected_after_reactivation(self, client, auth_headers):
        _create_user(client, auth_headers, "react_old", "ReactPass1")
        token = _login(client, "react_old", "ReactPass1").json()["token"]
        headers = {"Authorization": f"Bearer {token}"}
        assert client.get("/api/auth/me", headers=headers).status_code == 200

        assert client.delete("/api/auth/users/react_old", headers=auth_headers).status_code == 200
        with get_db() as conn:  # reactivation is a direct DB edit today
            conn.execute("UPDATE users SET is_active = 1 WHERE username = ?", ("react_old",))

        res = client.get("/api/auth/me", headers=headers)
        assert res.status_code == 401
        assert res.headers.get("WWW-Authenticate") == "Bearer"

    def test_new_login_works_after_reactivation(self, client, auth_headers):
        _create_user(client, auth_headers, "react_new", "ReactPass1")
        assert client.delete("/api/auth/users/react_new", headers=auth_headers).status_code == 200
        with get_db() as conn:
            conn.execute(
                "UPDATE users SET is_active = 1, tokens_valid_after = tokens_valid_after - 5 "
                "WHERE username = ?", ("react_new",),
            )

        token = _login(client, "react_new", "ReactPass1").json()["token"]
        assert client.get("/api/auth/me",
                          headers={"Authorization": f"Bearer {token}"}).status_code == 200

    def test_deactivation_sets_cutoff(self, client, auth_headers):
        _create_user(client, auth_headers, "react_cutoff", "ReactPass1")
        with get_db() as conn:
            before = conn.execute(
                "SELECT tokens_valid_after FROM users WHERE username = ?", ("react_cutoff",)
            ).fetchone()[0]
        assert before is None

        assert client.delete("/api/auth/users/react_cutoff", headers=auth_headers).status_code == 200
        with get_db() as conn:
            after = conn.execute(
                "SELECT tokens_valid_after FROM users WHERE username = ?", ("react_cutoff",)
            ).fetchone()[0]
        assert isinstance(after, int) and after > 0

    def test_token_issued_in_cutoff_second_rejected(self, client, auth_headers):
        """iat has one-second resolution, so a token from the deactivation
        second itself must also stay dead."""
        _create_user(client, auth_headers, "react_same_sec", "ReactPass1")
        token = create_token("react_same_sec")
        iat = auth_module.jwt.decode(token, options={"verify_signature": False})["iat"]
        with get_db() as conn:
            conn.execute("UPDATE users SET tokens_valid_after = ? WHERE username = ?",
                         (iat, "react_same_sec"))

        res = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert res.status_code == 401

    def test_token_without_iat_rejected_when_cutoff_set(self, client, auth_headers):
        _create_user(client, auth_headers, "react_no_iat", "ReactPass1")
        token = auth_module.jwt.encode(
            {"sub": "react_no_iat", "exp": 9999999999},
            auth_module.SECRET_KEY, algorithm=auth_module.ALGORITHM,
        )
        headers = {"Authorization": f"Bearer {token}"}
        assert client.get("/api/auth/me", headers=headers).status_code == 200

        with get_db() as conn:
            conn.execute("UPDATE users SET tokens_valid_after = 1 WHERE username = ?",
                         ("react_no_iat",))
        assert client.get("/api/auth/me", headers=headers).status_code == 401

    def test_null_cutoff_keeps_existing_tokens_working(self, client, auth_headers):
        """Adding the column must not log anyone out: NULL means no cutoff."""
        with get_db() as conn:
            row = conn.execute(
                "SELECT tokens_valid_after FROM users WHERE username = 'testuser'"
            ).fetchone()
        assert row[0] is None
        assert client.get("/api/auth/me", headers=auth_headers).status_code == 200


class TestTokensValidAfterMigration:
    def test_init_db_is_rerun_safe_and_keeps_cutoffs(self, client, auth_headers):
        from api.database import init_db
        _create_user(client, auth_headers, "mig_rerun", "ReactPass1")
        with get_db() as conn:
            conn.execute("UPDATE users SET tokens_valid_after = 1234 WHERE username = ?",
                         ("mig_rerun",))

        init_db()
        init_db()

        with get_db() as conn:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
            value = conn.execute(
                "SELECT tokens_valid_after FROM users WHERE username = ?", ("mig_rerun",)
            ).fetchone()[0]
        assert cols.count("tokens_valid_after") == 1
        assert value == 1234
        assert client.get("/api/auth/me", headers=auth_headers).status_code == 200

    def test_upgrade_from_pre_126_schema_keeps_everyone_signed_in(self, monkeypatch):
        """The real deploy: a DB without the column, live users and tokens,
        then init_db on every restart. Nobody may be signed out."""
        import sqlite3
        from contextlib import contextmanager
        import api.database as db

        pre126 = sqlite3.connect(":memory:", check_same_thread=False)
        pre126.row_factory = sqlite3.Row

        @contextmanager
        def _pre126_get_db():
            yield pre126
            pre126.commit()

        monkeypatch.setattr(db, "get_db", _pre126_get_db)
        with db.get_db() as conn:
            conn.executescript(db.SCHEMA)
            for migration in db.MIGRATIONS:
                if "tokens_valid_after" in migration:
                    continue
                try:
                    conn.execute(migration)
                except Exception:
                    pass
            conn.execute("INSERT INTO users (username, password_hash, is_admin) "
                         "VALUES ('up_jb', 'x', 1), ('up_hb', 'x', 1)")
        with db.get_db() as conn:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
        assert "tokens_valid_after" not in cols
        iats = {u: auth_module.jwt.decode(create_token(u, 720),
                                          options={"verify_signature": False})["iat"]
                for u in ("up_jb", "up_hb")}

        for _ in range(3):
            db.init_db()
            with db.get_db() as conn:
                cols = [r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
                cutoffs = [r[0] for r in conn.execute("SELECT tokens_valid_after FROM users")]
            assert cols.count("tokens_valid_after") == 1
            assert cutoffs == [None, None]
            for user, iat in iats.items():
                assert auth_module.token_rejection_reason(user, iat) is None


    def test_startup_fails_if_column_did_not_apply(self, monkeypatch):
        """Without the column every authenticated call would 500 while
        /api/health passes, so init_db must refuse to start instead."""
        import sqlite3
        from contextlib import contextmanager
        import api.database as db

        bare = sqlite3.connect(":memory:", check_same_thread=False)
        bare.row_factory = sqlite3.Row

        @contextmanager
        def _bare_get_db():
            yield bare
            bare.commit()

        monkeypatch.setattr(db, "get_db", _bare_get_db)
        monkeypatch.setattr(db, "MIGRATIONS",
                            [m for m in db.MIGRATIONS if "tokens_valid_after" not in m])
        with pytest.raises(RuntimeError, match="tokens_valid_after"):
            db.init_db()


class TestCutoffEdgeCases:
    def test_refresh_token_stays_dead_after_reactivation(self, client, auth_headers):
        _create_user(client, auth_headers, "react_refresh", "ReactPass1")
        refresh = _login(client, "react_refresh", "ReactPass1", remember_me=True).json()["refresh_token"]

        assert client.delete("/api/auth/users/react_refresh", headers=auth_headers).status_code == 200
        with get_db() as conn:
            conn.execute("UPDATE users SET is_active = 1 WHERE username = ?", ("react_refresh",))

        assert client.post("/api/auth/refresh", json={"refresh_token": refresh}).status_code == 401

    def test_non_integer_cutoff_fails_closed_with_401(self, client, auth_headers):
        """A cutoff hand-set as text (e.g. datetime('now')) must not 500."""
        _create_user(client, auth_headers, "react_text_cutoff", "ReactPass1")
        token = _login(client, "react_text_cutoff", "ReactPass1").json()["token"]
        with get_db() as conn:
            conn.execute("UPDATE users SET tokens_valid_after = datetime('now') WHERE username = ?",
                         ("react_text_cutoff",))

        res = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert res.status_code == 401

    def test_rejection_reasons_are_distinct(self, client, auth_headers):
        _create_user(client, auth_headers, "react_reason", "ReactPass1")
        with get_db() as conn:
            conn.execute("UPDATE users SET tokens_valid_after = 100 WHERE username = ?",
                         ("react_reason",))
        assert auth_module.token_rejection_reason("react_reason", 101) is None
        assert auth_module.token_rejection_reason("react_reason", 100) == "token issued before cutoff"
        assert auth_module.token_rejection_reason("react_reason", "101") == "token issued before cutoff"
        assert auth_module.token_rejection_reason("no_such_user_126", 101) == "inactive user"


class TestDirectDbDeactivation:
    """Issue #136: a row switched to inactive by a hand edit gets the same
    cutoff and refresh-token revocation as DELETE /api/auth/users/{username},
    and refresh tokens are checked against the cutoff too."""

    @staticmethod
    def _cutoff(username):
        with get_db() as conn:
            return conn.execute(
                "SELECT tokens_valid_after FROM users WHERE username = ?", (username,)
            ).fetchone()[0]

    @staticmethod
    def _set_active(username, value):
        with get_db() as conn:
            conn.execute("UPDATE users SET is_active = ? WHERE username = ?", (value, username))

    def test_hand_deactivation_sets_cutoff_and_revokes_refresh(self, client, auth_headers):
        _create_user(client, auth_headers, "db_deact", "DbDeact1")
        create_refresh_token("db_deact")
        create_refresh_token("db_deact")

        before = int(time.time())
        self._set_active("db_deact", 0)

        cutoff = self._cutoff("db_deact")
        assert isinstance(cutoff, int) and before <= cutoff <= int(time.time())
        with get_db() as conn:
            unrevoked = conn.execute(
                "SELECT COUNT(*) FROM refresh_tokens WHERE username = ? AND revoked = 0",
                ("db_deact",),
            ).fetchone()[0]
        assert unrevoked == 0

    def test_hand_deactivate_then_reactivate_keeps_old_tokens_dead(self, client, auth_headers):
        _create_user(client, auth_headers, "db_revive", "DbDeact1")
        data = _login(client, "db_revive", "DbDeact1", remember_me=True).json()
        headers = {"Authorization": f"Bearer {data['token']}"}
        assert client.get("/api/auth/me", headers=headers).status_code == 200

        self._set_active("db_revive", 0)
        self._set_active("db_revive", 1)

        res = client.get("/api/auth/me", headers=headers)
        assert res.status_code == 401
        assert res.headers.get("WWW-Authenticate") == "Bearer"
        assert client.post("/api/auth/refresh",
                           json={"refresh_token": data["refresh_token"]}).status_code == 401

    def test_hand_deactivation_leaves_other_users_alone(self, client, auth_headers):
        _create_user(client, auth_headers, "db_other", "DbDeact1")
        keep = create_refresh_token("testuser")

        self._set_active("db_other", 0)

        assert self._cutoff("testuser") is None
        assert validate_refresh_token(keep) == "testuser"
        assert client.get("/api/auth/me", headers=auth_headers).status_code == 200

    def test_cutoff_is_never_lowered(self, client, auth_headers):
        _create_user(client, auth_headers, "db_later", "DbDeact1")
        later = int(time.time()) + 1000
        with get_db() as conn:
            conn.execute("UPDATE users SET is_active = 0, tokens_valid_after = ? WHERE username = ?",
                         (later, "db_later"))
        assert self._cutoff("db_later") == later

    def test_api_deactivation_still_sets_cutoff(self, client, auth_headers):
        _create_user(client, auth_headers, "db_api", "DbDeact1")
        before = int(time.time())
        assert client.delete("/api/auth/users/db_api", headers=auth_headers).status_code == 200
        cutoff = self._cutoff("db_api")
        assert isinstance(cutoff, int) and before <= cutoff <= int(time.time())

    def test_updates_that_do_not_deactivate_leave_cutoff_alone(self, client, auth_headers):
        _create_user(client, auth_headers, "db_noop", "DbDeact1")
        raw = create_refresh_token("db_noop")
        with get_db() as conn:
            conn.execute("UPDATE users SET is_active = 1 WHERE username = ?", ("db_noop",))
            conn.execute("UPDATE users SET is_admin = 0 WHERE username = ?", ("db_noop",))
        assert self._cutoff("db_noop") is None
        assert validate_refresh_token(raw) == "db_noop"

        # Already inactive, set inactive again: the existing cutoff stays.
        self._set_active("db_noop", 0)
        with get_db() as conn:
            conn.execute("UPDATE users SET tokens_valid_after = 42 WHERE username = ?", ("db_noop",))
        self._set_active("db_noop", 0)
        assert self._cutoff("db_noop") == 42

    def test_unrevoked_refresh_token_from_before_cutoff_rejected(self, client, auth_headers):
        _create_user(client, auth_headers, "db_rt_old", "DbDeact1")
        raw = create_refresh_token("db_rt_old")
        with get_db() as conn:
            created_ts = conn.execute(
                "SELECT CAST(strftime('%s', created_at) AS INTEGER) FROM refresh_tokens "
                "WHERE username = ?", ("db_rt_old",),
            ).fetchone()[0]
            # The cutoff's own second counts as before it, like an access token's iat.
            conn.execute("UPDATE users SET tokens_valid_after = ? WHERE username = ?",
                         (created_ts, "db_rt_old"))
        assert validate_refresh_token(raw) is None
        assert client.post("/api/auth/refresh", json={"refresh_token": raw}).status_code == 401

        with get_db() as conn:
            conn.execute("UPDATE users SET tokens_valid_after = ? WHERE username = ?",
                         (created_ts - 1, "db_rt_old"))
        assert validate_refresh_token(raw) == "db_rt_old"

    def test_refresh_token_with_unreadable_created_at_fails_closed(self, client, auth_headers):
        _create_user(client, auth_headers, "db_rt_bad", "DbDeact1")
        raw = create_refresh_token("db_rt_bad")
        with get_db() as conn:
            conn.execute("UPDATE refresh_tokens SET created_at = 'garbage' WHERE username = ?",
                         ("db_rt_bad",))
        # With no cutoff the token still works, as before #136.
        assert validate_refresh_token(raw) == "db_rt_bad"
        with get_db() as conn:
            conn.execute("UPDATE users SET tokens_valid_after = 1 WHERE username = ?", ("db_rt_bad",))
        assert validate_refresh_token(raw) is None

    def test_refresh_works_after_reactivation_and_new_login(self, client, auth_headers):
        _create_user(client, auth_headers, "db_rt_new", "DbDeact1")
        self._set_active("db_rt_new", 0)
        with get_db() as conn:  # step the cutoff back so the new login is after it
            conn.execute(
                "UPDATE users SET is_active = 1, tokens_valid_after = tokens_valid_after - 5 "
                "WHERE username = ?", ("db_rt_new",),
            )

        refresh = _login(client, "db_rt_new", "DbDeact1", remember_me=True).json()["refresh_token"]
        res = client.post("/api/auth/refresh", json={"refresh_token": refresh})
        assert res.status_code == 200, res.text

    def test_refresh_token_issued_to_inactive_user_is_born_revoked(self, client, auth_headers):
        """A refresh that rotates after a deactivation commits must not leave
        a live row that would come back on reactivation."""
        _create_user(client, auth_headers, "db_race", "DbDeact1")
        self._set_active("db_race", 0)
        raw = create_refresh_token("db_race")
        self._set_active("db_race", 1)

        with get_db() as conn:
            revoked = conn.execute(
                "SELECT revoked FROM refresh_tokens WHERE username = ?", ("db_race",)
            ).fetchone()[0]
        assert revoked == 1
        assert validate_refresh_token(raw) is None

    def test_triggers_created_once_across_restarts(self):
        from api.database import init_db
        init_db()
        init_db()
        with get_db() as conn:
            names = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            )]
        assert names.count("users_deactivation_cutoff") == 1
        assert names.count("refresh_tokens_inactive_user") == 1
