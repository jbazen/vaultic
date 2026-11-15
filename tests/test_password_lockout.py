"""Wrong passwords on change-password and 2fa/disable hit the login lockout (issue #151).

Both endpoints check the signed-in user's password. They share login's
per-IP (5) and per-username (20) counters over 15 minutes, answer 429 while
locked without checking the password, and clear the counters on a right one.
The conftest fixture empties the counters around every test.
"""
from api import auth as auth_module
from api.auth import MAX_ATTEMPTS, MAX_ATTEMPTS_PER_USER, authenticate_user, create_token
from api.database import get_db
from api.routers import auth as auth_router

TESTCLIENT_IP = "testclient"


def _create_user(client, auth_headers, username, password):
    res = client.post("/api/auth/users", headers=auth_headers,
                      json={"username": username, "password": password})
    assert res.status_code == 200, res.text


def _headers(username, ip=None):
    headers = {"Authorization": f"Bearer {create_token(username)}"}
    if ip:
        headers["X-Forwarded-For"] = ip
    return headers


def _change(client, headers, current, new="NewLockPass9"):
    return client.post("/api/auth/change-password", headers=headers,
                       json={"current_password": current, "new_password": new})


def _disable(client, headers, password):
    return client.post("/api/auth/2fa/disable", headers=headers, json={"password": password})


def _enable_2fa(username):
    with get_db() as conn:
        conn.execute(
            "UPDATE users SET two_fa_enabled = 1, totp_secret = 'encrypted-placeholder' "
            "WHERE username = ?", (username,),
        )


def _two_fa_enabled(username):
    with get_db() as conn:
        return conn.execute(
            "SELECT two_fa_enabled FROM users WHERE username = ?", (username,)
        ).fetchone()[0]


class TestChangePasswordLockout:
    def test_locks_after_max_wrong_guesses_even_for_right_password(self, client, auth_headers):
        _create_user(client, auth_headers, "lk_cp1", "LockPass1")
        headers = _headers("lk_cp1")
        for _ in range(MAX_ATTEMPTS):
            assert _change(client, headers, "WrongPass1").status_code == 401
        res = _change(client, headers, "LockPass1")
        assert res.status_code == 429
        # Not a session failure: the UI must show the message, not sign out.
        assert "www-authenticate" not in res.headers
        assert authenticate_user("lk_cp1", "LockPass1")
        assert not authenticate_user("lk_cp1", "NewLockPass9")

    def test_wrong_guess_is_counted_against_ip_and_username(self, client, auth_headers):
        _create_user(client, auth_headers, "lk_cp2", "LockPass2")
        assert _change(client, _headers("lk_cp2"), "WrongPass1").status_code == 401
        assert len(auth_module._failed_attempts[TESTCLIENT_IP]) == 1
        assert len(auth_module._failed_by_username["lk_cp2"]) == 1

    def test_right_password_clears_counters(self, client, auth_headers):
        _create_user(client, auth_headers, "lk_cp3", "LockPass3")
        headers = _headers("lk_cp3")
        for _ in range(MAX_ATTEMPTS - 1):
            assert _change(client, headers, "WrongPass1").status_code == 401
        assert _change(client, headers, "LockPass3").status_code == 200
        assert TESTCLIENT_IP not in auth_module._failed_attempts
        assert "lk_cp3" not in auth_module._failed_by_username

    def test_weak_new_password_with_right_current_is_not_counted(self, client, auth_headers):
        _create_user(client, auth_headers, "lk_cp4", "LockPass4")
        assert _change(client, _headers("lk_cp4"), "LockPass4", new="weak").status_code == 400
        assert TESTCLIENT_IP not in auth_module._failed_attempts

    def test_password_not_checked_while_locked(self, client, auth_headers, monkeypatch):
        _create_user(client, auth_headers, "lk_cp5", "LockPass5")
        auth_module._failed_attempts[TESTCLIENT_IP] = [auth_module.time.time()] * MAX_ATTEMPTS
        calls = []
        monkeypatch.setattr(auth_router, "authenticate_user",
                            lambda *a: calls.append(a) or True)
        assert _change(client, _headers("lk_cp5"), "LockPass5").status_code == 429
        assert calls == []

    def test_per_username_lock_holds_across_ips(self, client, auth_headers):
        _create_user(client, auth_headers, "lk_cp6", "LockPass6")
        for i in range(MAX_ATTEMPTS_PER_USER):
            res = _change(client, _headers("lk_cp6", ip=f"203.0.113.{i + 1}"), "WrongPass1")
            assert res.status_code == 401
        res = _change(client, _headers("lk_cp6", ip="198.51.100.7"), "LockPass6")
        assert res.status_code == 429

    def test_lock_logs_security_events(self, client, auth_headers, monkeypatch):
        _create_user(client, auth_headers, "lk_cp7", "LockPass7")
        events = []
        monkeypatch.setattr(auth_router.security_log, "log_server_event", events.append)
        headers = _headers("lk_cp7")
        for _ in range(MAX_ATTEMPTS + 1):
            _change(client, headers, "WrongPass1")
        assert sum("WRONG_PASSWORD" in e and "action=change_password" in e for e in events) == MAX_ATTEMPTS
        assert any("RATE_LIMITED" in e and "user=lk_cp7" in e for e in events)
        assert not any("WrongPass1" in e for e in events)


class TestDisable2FALockout:
    def test_locks_after_max_wrong_guesses_and_2fa_stays_on(self, client, auth_headers):
        _create_user(client, auth_headers, "lk_2fa1", "LockPass1")
        _enable_2fa("lk_2fa1")
        headers = _headers("lk_2fa1")
        for _ in range(MAX_ATTEMPTS):
            assert _disable(client, headers, "WrongPass1").status_code == 401
        res = _disable(client, headers, "LockPass1")
        assert res.status_code == 429
        assert "www-authenticate" not in res.headers
        assert _two_fa_enabled("lk_2fa1") == 1

    def test_right_password_disables_and_clears_counters(self, client, auth_headers):
        _create_user(client, auth_headers, "lk_2fa2", "LockPass2")
        _enable_2fa("lk_2fa2")
        headers = _headers("lk_2fa2")
        assert _disable(client, headers, "WrongPass1").status_code == 401
        assert _disable(client, headers, "LockPass2").status_code == 200
        assert _two_fa_enabled("lk_2fa2") == 0
        assert TESTCLIENT_IP not in auth_module._failed_attempts
        assert "lk_2fa2" not in auth_module._failed_by_username


class TestSharedBudgetWithLogin:
    def test_login_failures_count_toward_change_password_lock(self, client, auth_headers):
        _create_user(client, auth_headers, "lk_mix1", "LockPass1")
        for _ in range(MAX_ATTEMPTS - 1):
            res = client.post("/api/auth/login",
                              json={"username": "lk_mix1", "password": "WrongPass1"})
            assert res.status_code == 401
        assert _change(client, _headers("lk_mix1"), "WrongPass1").status_code == 401
        assert _change(client, _headers("lk_mix1"), "LockPass1").status_code == 429

    def test_2fa_disable_failures_lock_login(self, client, auth_headers):
        _create_user(client, auth_headers, "lk_mix2", "LockPass2")
        headers = _headers("lk_mix2")
        for _ in range(MAX_ATTEMPTS):
            assert _disable(client, headers, "WrongPass1").status_code == 401
        res = client.post("/api/auth/login", json={"username": "lk_mix2", "password": "LockPass2"})
        assert res.status_code == 429
