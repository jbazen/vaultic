"""Wrong codes at /verify-2fa are locked out per IP and per username (#165, #156).

verify-2fa has its own counters: 5 wrong codes per IP (an IPv6 address counts
as its /64) or 10 per username in 15 minutes. A correct password at /login
doesn't clear them, and they don't lock password login. Every lockout counter
keys IPv6 by /64, and keys whose failures have aged out are swept away.
The conftest fixture empties every counter around every test.
"""
import time

import pyotp
import pytest

from api import auth as auth_module
from api.auth import (
    LOCKOUT_WINDOW, MAX_2FA_ATTEMPTS_PER_USER, MAX_ATTEMPTS, lockout_key,
)
from api.database import get_db
from api.encryption import encrypt


@pytest.fixture
def totp_user(client, auth_headers):
    """Make a user with 2FA on; return (username, password, TOTP)."""
    def make(username, password="TwoFaPass1"):
        res = client.post("/api/auth/users", headers=auth_headers,
                          json={"username": username, "password": password})
        assert res.status_code == 200, res.text
        secret = pyotp.random_base32()
        with get_db() as conn:
            conn.execute(
                "UPDATE users SET two_fa_enabled = 1, totp_secret = ? WHERE username = ?",
                (encrypt(secret), username),
            )
        return username, password, pyotp.TOTP(secret)
    return make


def _wrong_code(totp):
    """A code that is not valid in the accepted window."""
    now = time.time()
    valid = {totp.at(now + d) for d in (-2 * totp.interval, -totp.interval, 0,
                                         totp.interval, 2 * totp.interval)}
    return next(c for c in ("000000", "111111", "222222", "333333") if c not in valid)


def _login(client, username, password, ip=None):
    headers = {"X-Forwarded-For": ip} if ip else {}
    res = client.post("/api/auth/login", headers=headers,
                      json={"username": username, "password": password})
    assert res.status_code == 200, res.text
    assert res.json()["requires_2fa"] is True
    return res.json()["pending_token"]


def _verify(client, pending, code, ip=None):
    headers = {"X-Forwarded-For": ip} if ip else {}
    return client.post("/api/auth/verify-2fa", headers=headers,
                       json={"pending_token": pending, "code": code})


class TestLockoutKey:
    @pytest.mark.parametrize("ip, key", [
        ("203.0.113.7", "203.0.113.7"),
        ("2001:db8:1:2:aaaa:bbbb:cccc:dddd", "2001:db8:1:2::/64"),
        ("2001:db8:1:2::1", "2001:db8:1:2::/64"),
        ("2001:DB8:1:2::FFFF", "2001:db8:1:2::/64"),
        ("::ffff:203.0.113.7", "203.0.113.7"),
        ("testclient", "testclient"),
        ("unknown", "unknown"),
    ])
    def test_keys(self, ip, key):
        assert lockout_key(ip) == key

    def test_different_64s_are_different_keys(self):
        assert lockout_key("2001:db8:1:2::1") != lockout_key("2001:db8:1:3::1")


class TestVerify2FALockout:
    def test_locks_after_max_wrong_codes_even_for_right_code(self, client, totp_user):
        user, pw, totp = totp_user("tf_ip1")
        pending = _login(client, user, pw)
        for _ in range(MAX_ATTEMPTS):
            assert _verify(client, pending, _wrong_code(totp)).status_code == 401
        res = _verify(client, pending, totp.now())
        assert res.status_code == 429
        assert "Too many attempts" in res.json()["detail"]

    def test_relogin_does_not_reset_the_lock(self, client, totp_user):
        """#156: login + 4 wrong codes, repeated, used to never lock."""
        user, pw, totp = totp_user("tf_relogin")
        statuses = []
        for _ in range(3):
            pending = _login(client, user, pw)
            for _ in range(MAX_ATTEMPTS - 1):
                statuses.append(_verify(client, pending, _wrong_code(totp)).status_code)
        assert statuses[:MAX_ATTEMPTS] == [401] * MAX_ATTEMPTS
        assert set(statuses[MAX_ATTEMPTS:]) == {429}
        assert _verify(client, _login(client, user, pw), totp.now()).status_code == 429

    def test_per_username_lock_across_rotating_ips(self, client, totp_user):
        user, pw, totp = totp_user("tf_user")
        pending = _login(client, user, pw)
        for i in range(MAX_2FA_ATTEMPTS_PER_USER):
            ip = f"198.51.100.{i + 1}"
            assert _verify(client, pending, _wrong_code(totp), ip=ip).status_code == 401
        res = _verify(client, pending, totp.now(), ip="192.0.2.200")
        assert res.status_code == 429
        # Another user from a fresh address is not affected.
        other, opw, ototp = totp_user("tf_user_other")
        assert _verify(client, _login(client, other, opw), ototp.now(),
                       ip="192.0.2.201").status_code == 200

    def test_rotating_ipv6_in_one_64_is_one_key(self, client, totp_user):
        user, pw, totp = totp_user("tf_v6")
        pending = _login(client, user, pw)
        for i in range(MAX_ATTEMPTS):
            ip = f"2001:db8:aa:bb::{i + 1:x}"
            assert _verify(client, pending, _wrong_code(totp), ip=ip).status_code == 401
        # A new address in the same /64 is locked, even with a fresh user's right code.
        other, opw, ototp = totp_user("tf_v6_other")
        res = _verify(client, _login(client, other, opw), ototp.now(), ip="2001:db8:aa:bb::ffff")
        assert res.status_code == 429
        # Another /64 is not.
        assert _verify(client, _login(client, other, opw), ototp.now(),
                       ip="2001:db8:aa:bc::1").status_code == 200

    def test_wrong_codes_do_not_lock_password_login(self, client, totp_user):
        user, pw, totp = totp_user("tf_sep")
        pending = _login(client, user, pw)
        for i in range(MAX_2FA_ATTEMPTS_PER_USER):
            _verify(client, pending, _wrong_code(totp), ip=f"198.51.100.{i + 1}")
        assert not auth_module._failed_attempts and not auth_module._failed_by_username
        _login(client, user, pw)  # 200, still asks for the code

    def test_right_code_clears_counters(self, client, totp_user):
        user, pw, totp = totp_user("tf_clear")
        pending = _login(client, user, pw)
        for _ in range(MAX_ATTEMPTS - 1):
            assert _verify(client, pending, _wrong_code(totp)).status_code == 401
        assert _verify(client, pending, totp.now()).status_code == 200
        assert "testclient" not in auth_module._2fa_failed_by_ip
        assert user not in auth_module._2fa_failed_by_username

    def test_bad_pending_token_is_refused_while_ip_locked(self, client, totp_user):
        user, pw, totp = totp_user("tf_bad")
        pending = _login(client, user, pw)
        for _ in range(MAX_ATTEMPTS):
            _verify(client, pending, _wrong_code(totp))
        assert _verify(client, "bogus.token.here", "123456").status_code == 429


class TestLoginLockoutIPv6:
    def test_login_lock_counts_ipv6_by_64(self, client, auth_headers):
        res = client.post("/api/auth/users", headers=auth_headers,
                          json={"username": "lg_v6", "password": "LgV6Pass1"})
        assert res.status_code == 200
        for i in range(MAX_ATTEMPTS):
            res = client.post("/api/auth/login", headers={"X-Forwarded-For": f"2001:db8:cc::{i + 1:x}"},
                              json={"username": "lg_v6", "password": "Wrong1234"})
            assert res.status_code == 401
        res = client.post("/api/auth/login", headers={"X-Forwarded-For": "2001:db8:cc::abcd"},
                          json={"username": "lg_v6", "password": "LgV6Pass1"})
        assert res.status_code == 429


class TestStaleSweep:
    def test_record_sweeps_aged_out_keys(self, monkeypatch):
        now = 1_000_000.0
        monkeypatch.setattr(auth_module, "_last_sweep", 0.0)
        monkeypatch.setattr(auth_module.time, "time", lambda: now)
        auth_module._2fa_failed_by_ip["old-ip"] = [now - LOCKOUT_WINDOW - 1]
        auth_module._failed_by_username["old-user"] = [now - LOCKOUT_WINDOW]
        auth_module._own_pw_failed_by_ip["fresh-ip"] = [now - 10]
        auth_module.record_failed_attempt("192.0.2.1", "someone")
        assert "old-ip" not in auth_module._2fa_failed_by_ip
        assert "old-user" not in auth_module._failed_by_username
        assert auth_module._own_pw_failed_by_ip["fresh-ip"] == [now - 10]
        assert auth_module._failed_attempts["192.0.2.1"] == [now]

    def test_sweep_runs_at_most_once_per_interval(self, monkeypatch):
        clock = [1_000_000.0]
        monkeypatch.setattr(auth_module, "_last_sweep", 0.0)
        monkeypatch.setattr(auth_module.time, "time", lambda: clock[0])
        auth_module.record_2fa_failure("192.0.2.1", "a")  # sweeps, sets _last_sweep
        auth_module._2fa_failed_by_ip["old-ip"] = [clock[0] - LOCKOUT_WINDOW - 1]
        clock[0] += auth_module._SWEEP_INTERVAL - 1
        auth_module.record_2fa_failure("192.0.2.1", "a")
        assert "old-ip" in auth_module._2fa_failed_by_ip
        clock[0] += 1
        auth_module.record_2fa_failure("192.0.2.1", "a")
        assert "old-ip" not in auth_module._2fa_failed_by_ip
