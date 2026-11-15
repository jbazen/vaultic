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
            # Reactivation sets a fresh cutoff (#136); step it back afterwards
            # so a login in this same second is after it.
            conn.execute("UPDATE users SET is_active = 1 WHERE username = ?", ("react_new",))
            conn.execute("UPDATE users SET tokens_valid_after = tokens_valid_after - 5 "
                         "WHERE username = ?", ("react_new",))

        token = _login(client, "react_new", "ReactPass1").json()["token"]
        assert client.get("/api/auth/me",
                          headers={"Authorization": f"Bearer {token}"}).status_code == 200

    def test_deactivation_sets_cutoff(self, client, auth_headers):
        _create_user(client, auth_headers, "react_cutoff", "ReactPass1")
        with get_db() as conn:
            before = conn.execute(
                "SELECT tokens_valid_after FROM users WHERE username = ?", ("react_cutoff",)
            ).fetchone()[0]
        assert isinstance(before, int)  # new rows get a cutoff (#140)
        with get_db() as conn:  # put it in the past so the deactivation must move it
            conn.execute("UPDATE users SET tokens_valid_after = 1 WHERE username = ?",
                         ("react_cutoff",))

        assert client.delete("/api/auth/users/react_cutoff", headers=auth_headers).status_code == 200
        with get_db() as conn:
            after = conn.execute(
                "SELECT tokens_valid_after FROM users WHERE username = ?", ("react_cutoff",)
            ).fetchone()[0]
        assert isinstance(after, int) and after >= before

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

    def test_token_without_iat_rejected(self, client, auth_headers):
        """iat has been required since #146, cutoff or not."""
        _create_user(client, auth_headers, "react_no_iat", "ReactPass1")
        token = auth_module.jwt.encode(
            {"sub": "react_no_iat", "exp": 9999999999, "jti": "react-no-iat"},
            auth_module.SECRET_KEY, algorithm=auth_module.ALGORITHM,
        )
        headers = {"Authorization": f"Bearer {token}"}
        with get_db() as conn:  # a row from before #140, which gives new rows a cutoff
            conn.execute("UPDATE users SET tokens_valid_after = NULL WHERE username = ?",
                         ("react_no_iat",))
        assert client.get("/api/auth/me", headers=headers).status_code == 401

        with get_db() as conn:
            conn.execute("UPDATE users SET tokens_valid_after = 1 WHERE username = ?",
                         ("react_no_iat",))
        assert client.get("/api/auth/me", headers=headers).status_code == 401

    def test_null_cutoff_keeps_existing_tokens_working(self, client, auth_headers):
        """Adding the column must not log anyone out: NULL means no cutoff.
        Rows created before #140 keep NULL, so any token for them works."""
        _create_user(client, auth_headers, "react_null", "ReactPass1")
        old = auth_module.jwt.encode(
            {"sub": "react_null", "exp": 9999999999, "iat": 1, "jti": "react-null"},
            auth_module.SECRET_KEY, algorithm=auth_module.ALGORITHM,
        )
        headers = {"Authorization": f"Bearer {old}"}
        assert client.get("/api/auth/me", headers=headers).status_code == 401
        with get_db() as conn:
            conn.execute("UPDATE users SET tokens_valid_after = NULL WHERE username = ?",
                         ("react_null",))
        assert client.get("/api/auth/me", headers=headers).status_code == 200


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
        testuser_cutoff = self._cutoff("testuser")

        self._set_active("db_other", 0)

        assert self._cutoff("testuser") == testuser_cutoff
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
        before = self._cutoff("db_noop")
        with get_db() as conn:
            conn.execute("UPDATE users SET is_active = 1 WHERE username = ?", ("db_noop",))
            conn.execute("UPDATE users SET is_admin = 0 WHERE username = ?", ("db_noop",))
        assert self._cutoff("db_noop") == before
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
        # With no cutoff (a row from before #140) the token still works, as before #136.
        with get_db() as conn:
            conn.execute("UPDATE users SET tokens_valid_after = NULL WHERE username = ?", ("db_rt_bad",))
        assert validate_refresh_token(raw) == "db_rt_bad"
        with get_db() as conn:
            conn.execute("UPDATE users SET tokens_valid_after = 1 WHERE username = ?", ("db_rt_bad",))
        assert validate_refresh_token(raw) is None

    def test_refresh_works_after_reactivation_and_new_login(self, client, auth_headers):
        _create_user(client, auth_headers, "db_rt_new", "DbDeact1")
        self._set_active("db_rt_new", 0)
        self._set_active("db_rt_new", 1)
        with get_db() as conn:  # step the cutoff back so the new login is after it
            conn.execute("UPDATE users SET tokens_valid_after = tokens_valid_after - 5 "
                         "WHERE username = ?", ("db_rt_new",))

        refresh = _login(client, "db_rt_new", "DbDeact1", remember_me=True).json()["refresh_token"]
        res = client.post("/api/auth/refresh", json={"refresh_token": refresh})
        assert res.status_code == 200, res.text

    def test_refresh_token_issued_to_inactive_user_is_born_revoked(self, client, auth_headers):
        """A refresh that rotates after a deactivation commits must not leave
        a live row that would come back on reactivation."""
        _create_user(client, auth_headers, "db_race", "DbDeact1")
        self._set_active("db_race", 0)
        raw = create_refresh_token("db_race")

        with get_db() as conn:  # revoked at insert, before any reactivation
            revoked = conn.execute(
                "SELECT revoked FROM refresh_tokens WHERE username = ?", ("db_race",)
            ).fetchone()[0]
        assert revoked == 1

        self._set_active("db_race", 1)
        assert validate_refresh_token(raw) is None

    def test_access_token_minted_after_deactivation_dies_on_reactivation(self, client, auth_headers):
        """A login or refresh racing a hand deactivation can mint an access
        token whose iat is a second after the deactivation cutoff. Reactivation
        sets a fresh cutoff, so that token stays dead."""
        _create_user(client, auth_headers, "db_race_access", "DbDeact1")
        self._set_active("db_race_access", 0)
        with get_db() as conn:  # put the deactivation cutoff in the past
            conn.execute("UPDATE users SET tokens_valid_after = tokens_valid_after - 5 "
                         "WHERE username = ?", ("db_race_access",))
        raced = create_token("db_race_access")

        before = int(time.time())
        self._set_active("db_race_access", 1)

        assert self._cutoff("db_race_access") >= before
        res = client.get("/api/auth/me", headers={"Authorization": f"Bearer {raced}"})
        assert res.status_code == 401

    def test_reactivation_revokes_refresh_tokens(self, client, auth_headers):
        _create_user(client, auth_headers, "db_react_rt", "DbDeact1")
        self._set_active("db_react_rt", 0)
        raw = create_refresh_token("db_react_rt")
        with get_db() as conn:  # a row left live by hand, e.g. restored from a backup
            conn.execute("UPDATE refresh_tokens SET revoked = 0 WHERE username = ?", ("db_react_rt",))

        self._set_active("db_react_rt", 1)

        with get_db() as conn:
            unrevoked = conn.execute(
                "SELECT COUNT(*) FROM refresh_tokens WHERE username = ? AND revoked = 0",
                ("db_react_rt",),
            ).fetchone()[0]
        assert unrevoked == 0
        assert validate_refresh_token(raw) is None

    def test_any_non_1_is_active_counts_as_deactivation(self, client, auth_headers):
        _create_user(client, auth_headers, "db_null", "DbDeact1")
        with get_db() as conn:  # clear the #140 new-row cutoff so only the trigger can set one
            conn.execute("UPDATE users SET tokens_valid_after = NULL WHERE username = 'db_null'")
        self._set_active("db_null", None)
        assert isinstance(self._cutoff("db_null"), int)

    def test_multi_row_update_sets_each_cutoff(self, client, auth_headers):
        _create_user(client, auth_headers, "db_multi_a", "DbDeact1")
        _create_user(client, auth_headers, "db_multi_b", "DbDeact1")
        testuser_cutoff = self._cutoff("testuser")
        with get_db() as conn:
            conn.execute("UPDATE users SET tokens_valid_after = NULL "
                         "WHERE username IN ('db_multi_a', 'db_multi_b')")
            conn.execute("UPDATE users SET is_active = 0 WHERE username IN ('db_multi_a', 'db_multi_b')")
        assert isinstance(self._cutoff("db_multi_a"), int)
        assert isinstance(self._cutoff("db_multi_b"), int)
        assert self._cutoff("testuser") == testuser_cutoff

    def test_init_db_reruns_with_triggers_in_place(self, client, auth_headers):
        from api.database import init_db
        init_db()
        init_db()
        with get_db() as conn:
            names = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            )}
        assert {"users_active_change_cutoff", "refresh_tokens_inactive_user",
                "users_insert_cutoff", "users_rename_cutoff"} <= names
        assert client.get("/api/auth/me", headers=auth_headers).status_code == 200

    @pytest.mark.parametrize("trigger", [
        "users_active_change_cutoff", "refresh_tokens_inactive_user",
        "users_insert_cutoff", "users_rename_cutoff",
    ])
    def test_startup_fails_if_trigger_did_not_apply(self, monkeypatch, trigger):
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
                            [m for m in db.MIGRATIONS if trigger not in m])
        with pytest.raises(RuntimeError, match=trigger):
            db.init_db()


def _old_token(username, seconds_ago=5):
    """An access token minted for an earlier users row, before it was deleted."""
    now = int(time.time())
    return auth_module.jwt.encode(
        {"sub": username, "exp": now + 3600, "iat": now - seconds_ago, "jti": f"old-{username}"},
        auth_module.SECRET_KEY, algorithm=auth_module.ALGORITHM,
    )


def _cutoff_of(username):
    with get_db() as conn:
        return conn.execute(
            "SELECT tokens_valid_after FROM users WHERE username = ?", (username,)
        ).fetchone()[0]


def _unrevoked(username):
    with get_db() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM refresh_tokens WHERE username = ? AND revoked = 0", (username,)
        ).fetchone()[0]


def _me(client, token):
    return client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})


class TestRecreatedUserDoesNotReviveTokens:
    """Issue #140: a users row deleted by hand and then created again under the
    same username must not accept the old row's access or refresh tokens."""

    @staticmethod
    def _hand_delete(username):
        with get_db() as conn:
            conn.execute("DELETE FROM users WHERE username = ?", (username,))

    def test_old_tokens_dead_after_delete_and_api_recreate(self, client, auth_headers):
        _create_user(client, auth_headers, "rc_api", "RcPass111")
        old_refresh = _login(client, "rc_api", "RcPass111", remember_me=True).json()["refresh_token"]
        old_access = _old_token("rc_api")
        with get_db() as conn:  # the old row had no cutoff, like every pre-#140 row
            conn.execute("UPDATE users SET tokens_valid_after = NULL WHERE username = 'rc_api'")
        assert _me(client, old_access).status_code == 200

        self._hand_delete("rc_api")
        _create_user(client, auth_headers, "rc_api", "RcPass222")

        res = _me(client, old_access)
        assert res.status_code == 401
        assert res.headers.get("WWW-Authenticate") == "Bearer"
        assert _unrevoked("rc_api") == 0
        assert validate_refresh_token(old_refresh) is None
        assert client.post("/api/auth/refresh", json={"refresh_token": old_refresh}).status_code == 401

    def test_old_tokens_dead_after_delete_and_hand_recreate(self, client, auth_headers):
        _create_user(client, auth_headers, "rc_sql", "RcPass111")
        old_refresh = create_refresh_token("rc_sql")
        old_access = _old_token("rc_sql")

        self._hand_delete("rc_sql")
        with get_db() as conn:
            conn.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)",
                         ("rc_sql", hash_password("RcPass222")))

        assert _me(client, old_access).status_code == 401
        assert validate_refresh_token(old_refresh) is None

    def test_new_user_can_log_in_in_its_creation_second(self, client, auth_headers):
        """The cutoff is one second before the row, so a login straight after
        creating the account works, by password and by refresh token."""
        _create_user(client, auth_headers, "rc_fresh", "RcPass111")
        data = _login(client, "rc_fresh", "RcPass111", remember_me=True).json()

        iat = auth_module.jwt.decode(data["token"], options={"verify_signature": False})["iat"]
        assert _cutoff_of("rc_fresh") < iat
        assert _me(client, data["token"]).status_code == 200
        res = client.post("/api/auth/refresh", json={"refresh_token": data["refresh_token"]})
        assert res.status_code == 200, res.text

    def test_new_row_cutoff_is_just_before_creation(self, client, auth_headers):
        before = int(time.time())
        _create_user(client, auth_headers, "rc_when", "RcPass111")
        assert before - 1 <= _cutoff_of("rc_when") <= int(time.time()) - 1

    def test_token_from_before_creation_second_refused(self, client, auth_headers):
        _create_user(client, auth_headers, "rc_edge", "RcPass111")
        cutoff = _cutoff_of("rc_edge")
        now = int(time.time())
        at_cutoff = auth_module.jwt.encode(
            {"sub": "rc_edge", "exp": now + 3600, "iat": cutoff, "jti": "rc-edge"},
            auth_module.SECRET_KEY, algorithm=auth_module.ALGORITHM,
        )
        assert _me(client, at_cutoff).status_code == 401

    @pytest.mark.parametrize("verb", ["INSERT OR REPLACE", "REPLACE"])
    def test_old_tokens_dead_after_one_statement_replace(self, client, auth_headers, verb):
        """REPLACE deletes the old row and inserts a new one in one statement,
        the likeliest way to recreate a user by hand."""
        name = "rc_repl_" + verb.replace(" ", "_").lower()
        _create_user(client, auth_headers, name, "RcPass111")
        old_refresh = create_refresh_token(name)
        old_access = _old_token(name)
        with get_db() as conn:  # the old row had no cutoff, like every pre-#140 row
            conn.execute("UPDATE users SET tokens_valid_after = NULL WHERE username = ?", (name,))
        assert _me(client, old_access).status_code == 200

        with get_db() as conn:
            conn.execute(f"{verb} INTO users (username, password_hash) VALUES (?, ?)",
                         (name, hash_password("RcPass222")))

        assert _me(client, old_access).status_code == 401
        assert validate_refresh_token(old_refresh) is None

    def test_insert_keeps_a_later_cutoff_it_sets(self, client, auth_headers):
        later = int(time.time()) + 1000
        with get_db() as conn:
            conn.execute("INSERT INTO users (username, password_hash, tokens_valid_after) "
                         "VALUES (?, ?, ?)", ("rc_later", hash_password("RcPass111"), later))
        assert _cutoff_of("rc_later") == later

    def test_insert_leaves_other_users_alone(self, client, auth_headers):
        keep = create_refresh_token("testuser")
        testuser_cutoff = _cutoff_of("testuser")
        _create_user(client, auth_headers, "rc_other", "RcPass111")
        assert _cutoff_of("testuser") == testuser_cutoff
        assert validate_refresh_token(keep) == "testuser"
        assert client.get("/api/auth/me", headers=auth_headers).status_code == 200


class TestRenamedUserDoesNotReviveTokens:
    """Issue #140: a row renamed by hand onto a username whose old row was
    deleted must not accept that old row's tokens either."""

    @staticmethod
    def _rename(old, new):
        with get_db() as conn:
            conn.execute("UPDATE users SET username = ? WHERE username = ?", (new, old))

    def test_rename_onto_deleted_name_keeps_its_tokens_dead(self, client, auth_headers):
        _create_user(client, auth_headers, "rn_target", "RnPass111")
        old_refresh = create_refresh_token("rn_target")
        old_access = _old_token("rn_target")
        with get_db() as conn:
            conn.execute("DELETE FROM users WHERE username = 'rn_target'")
        _create_user(client, auth_headers, "rn_source", "RnPass111")
        with get_db() as conn:  # a row from before #140 has no cutoff
            conn.execute("UPDATE users SET tokens_valid_after = NULL WHERE username = 'rn_source'")

        before = int(time.time())
        self._rename("rn_source", "rn_target")

        assert _cutoff_of("rn_target") >= before
        assert _me(client, old_access).status_code == 401
        assert validate_refresh_token(old_refresh) is None

    def test_rename_revokes_refresh_tokens_of_both_names(self, client, auth_headers):
        _create_user(client, auth_headers, "rn_from", "RnPass111")
        mine = create_refresh_token("rn_from")
        with get_db() as conn:  # a stray live row left under the new name
            conn.execute("INSERT INTO refresh_tokens (token_hash, username, expires_at) "
                         "VALUES ('rn-stray', 'rn_to', '2999-01-01')")
            conn.execute("UPDATE refresh_tokens SET revoked = 0 WHERE token_hash = 'rn-stray'")

        self._rename("rn_from", "rn_to")

        assert _unrevoked("rn_from") == 0
        assert _unrevoked("rn_to") == 0
        assert validate_refresh_token(mine) is None

    def test_update_that_keeps_the_name_leaves_cutoff_alone(self, client, auth_headers):
        _create_user(client, auth_headers, "rn_same", "RnPass111")
        raw = create_refresh_token("rn_same")
        before = _cutoff_of("rn_same")
        with get_db() as conn:
            conn.execute("UPDATE users SET username = username WHERE username = 'rn_same'")
        assert _cutoff_of("rn_same") == before
        assert validate_refresh_token(raw) == "rn_same"

    def test_renamed_user_can_log_in_again(self, client, auth_headers):
        _create_user(client, auth_headers, "rn_login", "RnPass111")
        self._rename("rn_login", "rn_login2")
        with get_db() as conn:  # step the fail-closed cutoff back so a login this second is after it
            conn.execute("UPDATE users SET tokens_valid_after = tokens_valid_after - 5 "
                         "WHERE username = 'rn_login2'")
        token = _login(client, "rn_login2", "RnPass111").json()["token"]
        assert _me(client, token).status_code == 200


class TestPending2FATokenAcrossReactivation:
    """The #140 note: a 2FA pending token issued before a deactivation must not
    finish a login after a reactivation (the #136 trigger sets a fresh cutoff
    and decode_2fa_pending_token checks it, since #132)."""

    def test_pending_token_from_before_deactivation_refused_after_reactivation(
            self, client, auth_headers):
        from api.auth import create_2fa_pending_token, decode_2fa_pending_token
        _create_user(client, auth_headers, "pd_react", "PdPass111")
        pending = create_2fa_pending_token("pd_react")
        assert decode_2fa_pending_token(pending) == "pd_react"

        assert client.delete("/api/auth/users/pd_react", headers=auth_headers).status_code == 200
        with get_db() as conn:
            conn.execute("UPDATE users SET is_active = 1 WHERE username = 'pd_react'")

        assert decode_2fa_pending_token(pending) is None
        res = client.post("/api/auth/verify-2fa", json={"pending_token": pending, "code": "000000"})
        assert res.status_code == 401
