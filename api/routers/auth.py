from fastapi import APIRouter, HTTPException, status, Request, Depends
from fastapi.responses import Response
from pydantic import BaseModel
from api.auth import (
    authenticate_user, create_token, create_2fa_pending_token, decode_2fa_pending_token,
    hash_password, is_rate_limited, record_failed_attempt, clear_failed_attempts,
    get_user_2fa, generate_totp_setup, confirm_totp_enrollment, verify_totp_code,
)
from api.database import get_db
from api.dependencies import get_current_user, get_client_ip
from api import security_log

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


class Verify2FARequest(BaseModel):
    pending_token: str
    code: str


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

    if is_rate_limited(ip):
        security_log.log_server_event(f"RATE_LIMITED  ip={ip}  user={body.username}")
        raise HTTPException(status_code=429, detail="Too many failed attempts. Try again in 15 minutes.")

    ok = authenticate_user(body.username, body.password)
    security_log.log_login_attempt(ip, body.username, ok, ua)

    if not ok:
        record_failed_attempt(ip)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    clear_failed_attempts(ip)

    info = get_user_2fa(body.username)
    if info and info["two_fa_enabled"] and info["totp_secret"]:
        security_log.log_server_event(f"2FA_REQUIRED  ip={ip}  user={body.username}")
        pending_token = create_2fa_pending_token(body.username)
        return {"requires_2fa": True, "pending_token": pending_token}

    security_log.log_token_event(ip, body.username, "ISSUED")
    return {"token": create_token(body.username)}


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
    return {"token": create_token(username)}


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
    if len(body.new_password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
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


@router.delete("/2fa")
async def totp_disable(username: str = Depends(get_current_user)):
    with get_db() as conn:
        conn.execute(
            "UPDATE users SET two_fa_enabled = 0, totp_secret = NULL, totp_pending_secret = NULL WHERE username = ?",
            (username,)
        )
    security_log.log_server_event(f"2FA_DISABLED  username={username}")
    return {"status": "2fa_disabled"}


# ── User management ───────────────────────────────────────────────────────────

@router.get("/users")
async def list_users(_user: str = Depends(get_current_user)):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, username, two_fa_enabled, is_active, created_at FROM users ORDER BY created_at"
        ).fetchall()
    return [dict(row) for row in rows]


@router.post("/users")
async def create_user(body: CreateUserRequest, _user: str = Depends(get_current_user)):
    if len(body.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
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
async def delete_user(username: str, current_user: str = Depends(get_current_user)):
    if username == current_user:
        raise HTTPException(status_code=400, detail="Cannot delete your own account")
    with get_db() as conn:
        conn.execute("UPDATE users SET is_active = 0 WHERE username = ?", (username,))
    security_log.log_server_event(f"USER_DEACTIVATED  username={username}  by={current_user}")
    return {"status": "deactivated"}


# ── Security log ──────────────────────────────────────────────────────────────

@router.get("/security-log")
async def get_security_log(lines: int = 500, _user: str = Depends(get_current_user)):
    entries = security_log.tail(min(lines, 2000))
    return {"lines": entries, "total": len(entries)}
