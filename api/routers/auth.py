from typing import Optional

from fastapi import APIRouter, Body, HTTPException, status, Request, Depends
from fastapi.responses import Response
from pydantic import BaseModel
from api.auth import (
    authenticate_user, create_token, create_2fa_pending_token, decode_2fa_pending_token,
    hash_password, validate_password_strength, is_rate_limited, record_failed_attempt,
    clear_failed_attempts, get_user_2fa, generate_totp_setup, confirm_totp_enrollment,
    verify_totp_code, revoke_token,
    create_refresh_token, validate_refresh_token, rotate_refresh_token, revoke_refresh_token,
)
from api.database import get_db
from api.dependencies import get_current_user, get_client_ip, admin_required
from api import security_log

# Web sessions: 30-day access token, no refresh token. Re-login + 2FA after expiry.
_WEB_TOKEN_HOURS = 720       # 30 days

# Mobile sessions: short-lived access token paired with a 90-day rotating refresh token.
# The refresh token silently renews the access token — user never re-logs-in unless they
# explicitly log out or go 90 days without opening the app.
_MOBILE_TOKEN_HOURS = 1      # 1 hour; renewed silently by /auth/refresh

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str
    remember_me: bool = False   # True → mobile "keep me signed in" (issues refresh token)


class Verify2FARequest(BaseModel):
    pending_token: str
    code: str
    remember_me: bool = False   # Must match the value passed at the login step


class RefreshRequest(BaseModel):
    refresh_token: str          # Raw token issued at login (never the hash)


class ConfirmTOTPRequest(BaseModel):
    code: str


class CreateUserRequest(BaseModel):
    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


# ── Login ─────────────────────────────────────────────────────────────────────

@router.post("/login")
async def login(body: LoginRequest, request: Request):
    ip = get_client_ip(request)
    ua = request.headers.get("user-agent", "")

    if is_rate_limited(ip, body.username):
        security_log.log_server_event(f"RATE_LIMITED  ip={ip}  user={body.username}")
        raise HTTPException(status_code=429, detail="Too many failed attempts. Try again in 15 minutes.")

    ok = authenticate_user(body.username, body.password)
    security_log.log_login_attempt(ip, body.username, ok, ua)

    if not ok:
        record_failed_attempt(ip, body.username)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    clear_failed_attempts(ip, body.username)

    info = get_user_2fa(body.username)
    if info and info["two_fa_enabled"] and info["totp_secret"]:
        security_log.log_server_event(f"2FA_REQUIRED  ip={ip}  user={body.username}")
        pending_token = create_2fa_pending_token(body.username)
        return {"requires_2fa": True, "pending_token": pending_token}

    # No 2FA — issue token(s) directly based on remember_me
    security_log.log_token_event(ip, body.username, "ISSUED")
    return _issue_tokens(body.username, body.remember_me)


@router.post("/verify-2fa")
async def verify_2fa(body: Verify2FARequest, request: Request):
    ip = get_client_ip(request)

    if is_rate_limited(ip):
        raise HTTPException(status_code=429, detail="Too many attempts. Try again in 15 minutes.")

    # Decode the pending token to verify the user passed password auth
    username = decode_2fa_pending_token(body.pending_token)
    if not username:
        raise HTTPException(status_code=401, detail="Invalid or expired 2FA session — please log in again")

    ok = verify_totp_code(username, body.code)
    security_log.log_2fa_attempt(ip, username, ok)

    if not ok:
        record_failed_attempt(ip)
        raise HTTPException(status_code=401, detail="Invalid or expired code")

    clear_failed_attempts(ip)
    security_log.log_token_event(ip, username, "ISSUED")
    return _issue_tokens(username, body.remember_me)


# ── Token issuance helper ─────────────────────────────────────────────────────

def _issue_tokens(username: str, remember_me: bool) -> dict:
    """Return the correct token payload based on whether remember_me is set.

    Web (remember_me=False):
      - 30-day access token only. No refresh token. Re-login + 2FA after expiry.

    Mobile (remember_me=True):
      - 1-hour access token paired with a 90-day rotating refresh token.
      - The frontend silently calls /auth/refresh before each request when the
        access token is within 2 minutes of expiry. Rolling: each refresh extends
        the session another 90 days as long as the app is opened at least once.
    """
    if remember_me:
        return {
            "token": create_token(username, hours=_MOBILE_TOKEN_HOURS),
            "refresh_token": create_refresh_token(username),
        }
    return {"token": create_token(username, hours=_WEB_TOKEN_HOURS)}


# ── Refresh ───────────────────────────────────────────────────────────────────

@router.post("/refresh")
async def refresh_token(body: RefreshRequest, request: Request):
    """Exchange a valid refresh token for a new access token + rotated refresh token.

    Rotation: the supplied refresh token is immediately revoked and replaced.
    If an attacker steals the token and uses it first, the legitimate client's
    next refresh attempt will fail (token already revoked) — this detects theft.
    """
    username = validate_refresh_token(body.refresh_token)
    if not username:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token — please log in again")

    new_refresh = rotate_refresh_token(body.refresh_token, username)
    new_access  = create_token(username, hours=_MOBILE_TOKEN_HOURS)

    ip = get_client_ip(request)
    security_log.log_token_event(ip, username, "REFRESHED")
    return {"token": new_access, "refresh_token": new_refresh}


# ── Logout ────────────────────────────────────────────────────────────────────

@router.post("/logout")
async def logout(
    request: Request,
    username: str = Depends(get_current_user),
    refresh_token: Optional[str] = Body(default=None, embed=True),
):
    """Revoke the current access token and (if provided) the refresh token.

    Mobile clients should always send their refresh_token in the body so the
    server-side record is invalidated immediately — otherwise the 90-day window
    could still be used to issue new access tokens after logout.
    """
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        revoke_token(auth_header[7:])
    if refresh_token:
        revoke_refresh_token(refresh_token)
    ip = get_client_ip(request)
    security_log.log_token_event(ip, username, "REVOKED")
    return {"status": "logged_out"}


# ── Profile ───────────────────────────────────────────────────────────────────

@router.get("/me")
async def me(username: str = Depends(get_current_user)):
    with get_db() as conn:
        row = conn.execute(
            "SELECT username, two_fa_enabled FROM users WHERE username = ?", (username,)
        ).fetchone()
    return dict(row) if row else {"username": username, "two_fa_enabled": 0}


@router.post("/change-password")
async def change_password(body: ChangePasswordRequest, username: str = Depends(get_current_user)):
    if not authenticate_user(username, body.current_password):
        raise HTTPException(status_code=401, detail="Current password is incorrect")
    pw_err = validate_password_strength(body.new_password)
    if pw_err:
        raise HTTPException(status_code=400, detail=pw_err)
    with get_db() as conn:
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE username = ?",
            (hash_password(body.new_password), username)
        )
    security_log.log_server_event(f"PASSWORD_CHANGED  username={username}")
    return {"status": "updated"}


# ── TOTP 2FA ──────────────────────────────────────────────────────────────────

@router.post("/2fa/setup")
async def totp_setup(username: str = Depends(get_current_user)):
    """Generate a new TOTP secret + QR code SVG. Pending until confirmed."""
    _uri, svg = generate_totp_setup(username)
    security_log.log_server_event(f"2FA_SETUP_STARTED  username={username}")
    return Response(content=svg, media_type="image/svg+xml")


@router.post("/2fa/confirm")
async def totp_confirm(body: ConfirmTOTPRequest, username: str = Depends(get_current_user), request: Request = None):
    ip = get_client_ip(request) if request else "unknown"
    ok = confirm_totp_enrollment(username, body.code)
    security_log.log_2fa_attempt(ip, username, ok)
    if not ok:
        raise HTTPException(status_code=401, detail="Invalid code — make sure your authenticator app is in sync")
    security_log.log_server_event(f"2FA_ENABLED  username={username}")
    return {"status": "2fa_enabled"}


class Disable2FARequest(BaseModel):
    password: str


@router.post("/2fa/disable")
async def totp_disable(body: Disable2FARequest, username: str = Depends(get_current_user)):
    """Disable 2FA — requires current password to prevent abuse of stolen sessions."""
    if not authenticate_user(username, body.password):
        raise HTTPException(status_code=401, detail="Password is incorrect")
    with get_db() as conn:
        conn.execute(
            "UPDATE users SET two_fa_enabled = 0, totp_secret = NULL, totp_pending_secret = NULL WHERE username = ?",
            (username,)
        )
    security_log.log_server_event(f"2FA_DISABLED  username={username}")
    return {"status": "2fa_disabled"}


# ── User management ───────────────────────────────────────────────────────────

@router.get("/users")
async def list_users(_user: str = Depends(admin_required)):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, username, two_fa_enabled, is_admin, is_active, created_at FROM users WHERE is_active = 1 ORDER BY created_at"
        ).fetchall()
    return [dict(row) for row in rows]


@router.post("/users")
async def create_user(body: CreateUserRequest, _user: str = Depends(admin_required)):
    pw_err = validate_password_strength(body.password)
    if pw_err:
        raise HTTPException(status_code=400, detail=pw_err)
    with get_db() as conn:
        existing = conn.execute("SELECT id FROM users WHERE username = ?", (body.username,)).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail="Username already exists")
        conn.execute(
            "INSERT INTO users (username, password_hash) VALUES (?, ?)",
            (body.username, hash_password(body.password))
        )
    security_log.log_server_event(f"USER_CREATED  username={body.username}  by={_user}")
    return {"status": "created", "username": body.username}


@router.delete("/users/{username}")
async def delete_user(username: str, current_user: str = Depends(admin_required)):
    if username == current_user:
        raise HTTPException(status_code=400, detail="Cannot delete your own account")
    with get_db() as conn:
        conn.execute("UPDATE users SET is_active = 0 WHERE username = ?", (username,))
    security_log.log_server_event(f"USER_DEACTIVATED  username={username}  by={current_user}")
    return {"status": "deactivated"}


# ── Security log ──────────────────────────────────────────────────────────────

@router.get("/security-log")
async def get_security_log(lines: int = 500, _user: str = Depends(admin_required)):
    entries = security_log.tail(min(lines, 2000))
    return {"lines": entries, "total": len(entries)}
