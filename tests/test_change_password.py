"""Changing the password ends every other session (issue #132).

The change sets users.tokens_valid_after (the #126 cutoff) and revokes all
refresh tokens, then hands the caller replacement tokens so the device that
made the change stays signed in.
"""
import asyncio
import time

import jwt

from api import auth as auth_module
from api.auth import (
    SECRET_KEY, ALGORITHM, create_2fa_pending_token, create_refresh_token,
    decode_2fa_pending_token, token_rejection_reason, validate_refresh_token,
)
from api.database import get_db
from api.routers.auth import _wait_until_past


def _create_user(client, auth_headers, username, password):
    res = client.post("/api/auth/users", headers=auth_headers,
                      json={"username": username, "password": password})
    assert res.status_code == 200, res.text


def _login(client, username, password, remember_me=False):
    res = client.post("/api/auth/login", json={
        "username": username, "password": password, "remember_me": remember_me,
    })
    assert res.status_code == 200, res.text
    return res.json()


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _change(client, token, current, new, remember_me=None):
    body = {"current_password": current, "new_password": new}
    if remember_me is not None:
        body["remember_me"] = remember_me
    return client.post("/api/auth/change-password", headers=_bearer(token), json=body)


def _cutoff(username):
    with get_db() as conn:
        return conn.execute(
            "SELECT tokens_valid_after FROM users WHERE username = ?", (username,)
        ).fetchone()["tokens_valid_after"]


class TestOtherSessionsEnd:
    def test_other_web_session_rejected(self, client, auth_headers):
        _create_user(client, auth_headers, "cp_web", "CpWebPass1")
        mine = _login(client, "cp_web", "CpWebPass1")["token"]
        other = _login(client, "cp_web", "CpWebPass1")["token"]
        assert client.get("/api/auth/me", headers=_bearer(other)).status_code == 200

        assert _change(client, mine, "CpWebPass1", "CpWebPass2").status_code == 200

        res = client.get("/api/auth/me", headers=_bearer(other))
        assert res.status_code == 401
        assert res.headers.get("WWW-Authenticate") == "Bearer"

    def test_callers_old_token_rejected_new_token_works(self, client, auth_headers):
        _create_user(client, auth_headers, "cp_self", "CpSelfPass1")
        old = _login(client, "cp_self", "CpSelfPass1")["token"]

        data = _change(client, old, "CpSelfPass1", "CpSelfPass2").json()

        assert client.get("/api/auth/me", headers=_bearer(old)).status_code == 401
        res = client.get("/api/auth/me", headers=_bearer(data["token"]))
        assert res.status_code == 200
        assert res.json()["username"] == "cp_self"

    def test_other_mobile_session_cannot_refresh(self, client, auth_headers):
        _create_user(client, auth_headers, "cp_mobile", "CpMobPass1")
        mine = _login(client, "cp_mobile", "CpMobPass1")["token"]
        phone = _login(client, "cp_mobile", "CpMobPass1", remember_me=True)

        assert _change(client, mine, "CpMobPass1", "CpMobPass2").status_code == 200

        assert client.get("/api/auth/me", headers=_bearer(phone["token"])).status_code == 401
        res = client.post("/api/auth/refresh", json={"refresh_token": phone["refresh_token"]})
        assert res.status_code == 401
        with get_db() as conn:
            live = conn.execute(
                """SELECT COUNT(*) FROM refresh_tokens
                    WHERE username = 'cp_mobile' AND revoked = 0"""
            ).fetchone()[0]
        assert live == 0

    def test_refresh_token_left_unrevoked_still_refused(self, client, auth_headers):
        """Even a refresh row the revoke missed is dead: created before the cutoff."""
        _create_user(client, auth_headers, "cp_stray", "CpStrayPass1")
        mine = _login(client, "cp_stray", "CpStrayPass1")["token"]
        stray = create_refresh_token("cp_stray")
        assert _change(client, mine, "CpStrayPass1", "CpStrayPass2").status_code == 200
        with get_db() as conn:
            conn.execute("UPDATE refresh_tokens SET revoked = 0 WHERE username = 'cp_stray'")
        assert validate_refresh_token(stray) is None

    def test_old_password_no_longer_logs_in(self, client, auth_headers):
        _create_user(client, auth_headers, "cp_pw", "CpPwPass1")
        mine = _login(client, "cp_pw", "CpPwPass1")["token"]
        assert _change(client, mine, "CpPwPass1", "CpPwPass2").status_code == 200

        res = client.post("/api/auth/login",
                          json={"username": "cp_pw", "password": "CpPwPass1"})
        assert res.status_code == 401
        assert _login(client, "cp_pw", "CpPwPass2")["token"]

    def test_other_users_sessions_untouched(self, client, auth_headers):
        _create_user(client, auth_headers, "cp_a", "CpAPass1")
        _create_user(client, auth_headers, "cp_b", "CpBPass1")
        a = _login(client, "cp_a", "CpAPass1")["token"]
        b = _login(client, "cp_b", "CpBPass1", remember_me=True)
        b_cutoff = _cutoff("cp_b")

        assert _change(client, a, "CpAPass1", "CpAPass2").status_code == 200

        assert client.get("/api/auth/me", headers=_bearer(b["token"])).status_code == 200
        assert validate_refresh_token(b["refresh_token"]) == "cp_b"
        assert _cutoff("cp_b") == b_cutoff


class TestReplacementTokens:
    def test_web_caller_gets_web_token_only(self, client, auth_headers):
        _create_user(client, auth_headers, "cp_rweb", "CpRwebPass1")
        mine = _login(client, "cp_rweb", "CpRwebPass1")["token"]

        data = _change(client, mine, "CpRwebPass1", "CpRwebPass2").json()

        assert data["ok"] is True
        assert "refresh_token" not in data
        claims = jwt.decode(data["token"], SECRET_KEY, algorithms=[ALGORITHM])
        assert claims["exp"] - claims["iat"] == 720 * 3600

    def test_mobile_caller_gets_working_refresh_token(self, client, auth_headers):
        _create_user(client, auth_headers, "cp_rmob", "CpRmobPass1")
        phone = _login(client, "cp_rmob", "CpRmobPass1", remember_me=True)

        data = _change(client, phone["token"], "CpRmobPass1", "CpRmobPass2",
                       remember_me=True).json()

        claims = jwt.decode(data["token"], SECRET_KEY, algorithms=[ALGORITHM])
        assert claims["exp"] - claims["iat"] == 3600
        assert client.get("/api/auth/me", headers=_bearer(data["token"])).status_code == 200
        res = client.post("/api/auth/refresh", json={"refresh_token": data["refresh_token"]})
        assert res.status_code == 200
        # The phone's pre-change refresh token is still dead.
        res = client.post("/api/auth/refresh", json={"refresh_token": phone["refresh_token"]})
        assert res.status_code == 401

    def test_new_token_issued_after_cutoff(self, client, auth_headers):
        _create_user(client, auth_headers, "cp_iat", "CpIatPass1")
        mine = _login(client, "cp_iat", "CpIatPass1")["token"]

        data = _change(client, mine, "CpIatPass1", "CpIatPass2").json()

        claims = jwt.decode(data["token"], SECRET_KEY, algorithms=[ALGORITHM])
        cutoff = _cutoff("cp_iat")
        assert isinstance(cutoff, int)
        assert claims["iat"] > cutoff
        # A token minted in the cutoff second itself is refused: no "now - 1" slack.
        assert token_rejection_reason("cp_iat", cutoff) == "token issued before cutoff"


class TestCutoff:
    def test_cutoff_set_to_change_time(self, client, auth_headers):
        _create_user(client, auth_headers, "cp_cut", "CpCutPass1")
        mine = _login(client, "cp_cut", "CpCutPass1")["token"]
        before = int(time.time())
        assert _change(client, mine, "CpCutPass1", "CpCutPass2").status_code == 200
        assert before <= _cutoff("cp_cut") <= int(time.time())

    def test_later_cutoff_not_lowered(self, client, auth_headers):
        _create_user(client, auth_headers, "cp_later", "CpLaterPass1")
        later = int(time.time()) + 1000
        with get_db() as conn:
            conn.execute("UPDATE users SET tokens_valid_after = ? WHERE username = 'cp_later'",
                         (later,))
        auth_module.change_password_and_end_sessions("cp_later", "CpLaterPass3")
        assert _cutoff("cp_later") == later


class TestNothingChangesOnRejectedRequest:
    def test_wrong_current_password_keeps_sessions(self, client, auth_headers):
        _create_user(client, auth_headers, "cp_wrong", "CpWrongPass1")
        mine = _login(client, "cp_wrong", "CpWrongPass1")["token"]
        phone = _login(client, "cp_wrong", "CpWrongPass1", remember_me=True)
        before = _cutoff("cp_wrong")

        res = _change(client, mine, "NotThePass1", "CpWrongPass2")

        assert res.status_code == 401
        # A wrong current password is not an auth failure: no forced logout.
        assert res.headers.get("WWW-Authenticate") is None
        assert _cutoff("cp_wrong") == before
        assert client.get("/api/auth/me", headers=_bearer(mine)).status_code == 200
        assert validate_refresh_token(phone["refresh_token"]) == "cp_wrong"

    def test_weak_new_password_keeps_sessions(self, client, auth_headers):
        _create_user(client, auth_headers, "cp_weak", "CpWeakPass1")
        mine = _login(client, "cp_weak", "CpWeakPass1")["token"]
        before = _cutoff("cp_weak")

        res = _change(client, mine, "CpWeakPass1", "weak")

        assert res.status_code == 400
        assert _cutoff("cp_weak") == before
        assert client.get("/api/auth/me", headers=_bearer(mine)).status_code == 200

    def test_requires_auth(self, client):
        res = client.post("/api/auth/change-password", json={
            "current_password": "a", "new_password": "b"})
        assert res.status_code == 401


class TestPending2FAToken:
    def test_pending_token_from_before_change_refused(self, client, auth_headers):
        _create_user(client, auth_headers, "cp_2fa", "Cp2faPass1")
        mine = _login(client, "cp_2fa", "Cp2faPass1")["token"]
        pending = create_2fa_pending_token("cp_2fa")
        assert decode_2fa_pending_token(pending) == "cp_2fa"

        assert _change(client, mine, "Cp2faPass1", "Cp2faPass2").status_code == 200

        assert decode_2fa_pending_token(pending) is None
        res = client.post("/api/auth/verify-2fa",
                          json={"pending_token": pending, "code": "000000"})
        assert res.status_code == 401
        assert "log in again" in res.json()["detail"]

    def test_pending_token_after_change_accepted(self, client, auth_headers):
        _create_user(client, auth_headers, "cp_2fa_new", "Cp2faNew1")
        mine = _login(client, "cp_2fa_new", "Cp2faNew1")["token"]
        assert _change(client, mine, "Cp2faNew1", "Cp2faNew2").status_code == 200
        # The change already waited past the cutoff second.
        assert decode_2fa_pending_token(create_2fa_pending_token("cp_2fa_new")) == "cp_2fa_new"

    def test_pending_token_carries_iat(self):
        claims = jwt.decode(create_2fa_pending_token("anyone"), SECRET_KEY,
                            algorithms=[ALGORITHM])
        assert isinstance(claims["iat"], int)

    def test_pending_token_without_iat_ok_when_no_cutoff(self, client, auth_headers):
        """Logins mid-2FA at deploy time (old tokens have no iat) still finish."""
        _create_user(client, auth_headers, "cp_noiat", "CpNoiatPass1")
        with get_db() as conn:  # a row from before #140, which gives new rows a cutoff
            conn.execute("UPDATE users SET tokens_valid_after = NULL WHERE username = 'cp_noiat'")
        old_style = jwt.encode({"sub": "cp_noiat", "purpose": "2fa_pending",
                                "exp": int(time.time()) + 300}, SECRET_KEY, algorithm=ALGORITHM)
        assert decode_2fa_pending_token(old_style) == "cp_noiat"

    def test_pending_token_without_iat_refused_when_cutoff_set(self, client, auth_headers):
        _create_user(client, auth_headers, "cp_noiat2", "CpNoiatPass1")
        with get_db() as conn:
            conn.execute("UPDATE users SET tokens_valid_after = 1 WHERE username = 'cp_noiat2'")
        old_style = jwt.encode({"sub": "cp_noiat2", "purpose": "2fa_pending",
                                "exp": int(time.time()) + 300}, SECRET_KEY, algorithm=ALGORITHM)
        assert decode_2fa_pending_token(old_style) is None

    def test_pending_token_for_inactive_user_refused(self, client, auth_headers):
        _create_user(client, auth_headers, "cp_2fa_gone", "Cp2faGone1")
        pending = create_2fa_pending_token("cp_2fa_gone")
        assert client.delete("/api/auth/users/cp_2fa_gone", headers=auth_headers).status_code == 200
        assert decode_2fa_pending_token(pending) is None


class TestWaitUntilPast:
    def test_returns_at_once_for_past_cutoff(self):
        async def timed():
            start = time.monotonic()
            await _wait_until_past(int(time.time()) - 5)
            return time.monotonic() - start
        assert asyncio.run(timed()) < 0.5

    def test_waits_into_next_second(self):
        cutoff = int(time.time())
        asyncio.run(_wait_until_past(cutoff))
        assert int(time.time()) > cutoff

    def test_gives_up_on_far_future_cutoff(self):
        """A clock that stepped backwards must not hang the request."""
        async def timed():
            start = time.monotonic()
            await _wait_until_past(int(time.time()) + 600, limit=0.3)
            return time.monotonic() - start
        assert asyncio.run(timed()) < 1.5
