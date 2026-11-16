import os
import io
import re
import time
import uuid
import hashlib
import ipaddress
import secrets
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import bcrypt
import jwt
import pyotp
import qrcode
import qrcode.image.svg


def _load_env():
    env_path = Path(__file__).parent.parent / ".env"
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())


_load_env()

SECRET_KEY = os.environ.get("JWT_SECRET", "")
if not SECRET_KEY and not os.environ.get("TESTING"):
    raise RuntimeError("JWT_SECRET is not set — refusing to start with unsigned tokens")
ALGORITHM = "HS256"
TOKEN_EXPIRE_HOURS = int(os.environ.get("JWT_EXPIRE_HOURS", "24"))

_failed_attempts: dict[str, list[float]] = defaultdict(list)
_failed_by_username: dict[str, list[float]] = defaultdict(list)
# Wrong passwords typed by a user who is already signed in (change password,
# turn off 2FA). Same limits as login but separate counters, so wrong guesses
# at /login, which need no session, can't block the owner's own
# sign-out-everywhere control (issue #151).
_own_pw_failed_by_ip: dict[str, list[float]] = defaultdict(list)
_own_pw_failed_by_username: dict[str, list[float]] = defaultdict(list)
# Wrong codes at /verify-2fa. Own counters, so a correct password at /login
# (which clears the login counters) can't reset them (#156), and wrong codes
# can't lock the owner out of password login (#165).
_2fa_failed_by_ip: dict[str, list[float]] = defaultdict(list)
_2fa_failed_by_username: dict[str, list[float]] = defaultdict(list)
_Counter = dict[str, list[float]]
_ALL_COUNTERS: tuple[_Counter, ...] = (
    _failed_attempts, _failed_by_username,
    _own_pw_failed_by_ip, _own_pw_failed_by_username,
    _2fa_failed_by_ip, _2fa_failed_by_username,
)
MAX_ATTEMPTS = 5
MAX_ATTEMPTS_PER_USER = 20  # Per-username across all IPs
# Wrong 2FA codes per username across all IPs. Lower than the password limit:
# whoever reaches this step already has the password, and a 6-digit code
# (accepted in a 3-step window) is the only thing left to guess.
MAX_2FA_ATTEMPTS_PER_USER = 10
LOCKOUT_WINDOW = 15 * 60
# How often a recorded failure also sweeps every counter for keys whose
# failures have all aged out, so keys that are never checked again (a rotated
# IPv6 address, a made-up username) don't pile up (#165).
_SWEEP_INTERVAL = 60
_last_sweep = 0.0


def lockout_key(ip: str) -> str:
    """The key an IP's failures are counted under.

    An IPv6 address counts as its /64: one host is usually handed a whole /64,
    so counting single addresses would give it 2^64 fresh lockouts (#165).
    An IPv4-mapped IPv6 address counts as the IPv4 address. Anything else,
    such as IPv4 or "unknown", is used as is.
    """
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    if isinstance(addr, ipaddress.IPv6Address):
        if addr.ipv4_mapped:
            return str(addr.ipv4_mapped)
        return str(ipaddress.IPv6Network((int(addr) >> 64 << 64, 64)))
    return str(addr)


def _over_limit(counter: _Counter, key: str, limit: int) -> bool:
    """Drop failures older than the window (and the key once empty), then compare."""
    now = time.time()
    counter[key] = [t for t in counter[key] if now - t < LOCKOUT_WINDOW]
    if not counter[key]:
        counter.pop(key, None)
        return False
    return len(counter[key]) >= limit


def _sweep_stale(now: float) -> None:
    """Drop every key whose newest failure is outside the window, at most
    once per _SWEEP_INTERVAL."""
    global _last_sweep
    if now - _last_sweep < _SWEEP_INTERVAL:
        return
    _last_sweep = now
    for counter in _ALL_COUNTERS:
        for key in [k for k, times in counter.items() if not times or now - times[-1] >= LOCKOUT_WINDOW]:
            counter.pop(key, None)


def _is_locked(by_ip: _Counter, by_username: _Counter, ip: str, username: str,
               user_limit: int = MAX_ATTEMPTS_PER_USER) -> bool:
    if _over_limit(by_ip, lockout_key(ip), MAX_ATTEMPTS):
        return True
    return bool(username) and _over_limit(by_username, username, user_limit)


def _record(by_ip: _Counter, by_username: _Counter, ip: str, username: str) -> None:
    now = time.time()
    _sweep_stale(now)
    by_ip[lockout_key(ip)].append(now)
    if username:
        by_username[username].append(now)


def _clear(by_ip: _Counter, by_username: _Counter, ip: str, username: str) -> None:
    by_ip.pop(lockout_key(ip), None)
    if username:
        by_username.pop(username, None)


def is_rate_limited(ip: str, username: str = "") -> bool:
    """Check both per-IP and per-username login rate limits."""
    return _is_locked(_failed_attempts, _failed_by_username, ip, username)


def record_failed_attempt(ip: str, username: str = ""):
    _record(_failed_attempts, _failed_by_username, ip, username)


def clear_failed_attempts(ip: str, username: str = ""):
    _clear(_failed_attempts, _failed_by_username, ip, username)


def is_own_password_locked(ip: str, username: str) -> bool:
    """Signed-in password checks: same limits as login, own counters (#151)."""
    return _is_locked(_own_pw_failed_by_ip, _own_pw_failed_by_username, ip, username)


def record_own_password_failure(ip: str, username: str) -> None:
    _record(_own_pw_failed_by_ip, _own_pw_failed_by_username, ip, username)


def clear_own_password_failures(ip: str, username: str) -> None:
    _clear(_own_pw_failed_by_ip, _own_pw_failed_by_username, ip, username)


def is_2fa_locked(ip: str, username: str = "") -> bool:
    """verify-2fa: 5 wrong codes per IP (IPv6 /64) or 10 per username in
    15 minutes, on counters a password login doesn't touch (#156, #165)."""
    return _is_locked(_2fa_failed_by_ip, _2fa_failed_by_username, ip, username,
                      user_limit=MAX_2FA_ATTEMPTS_PER_USER)


def record_2fa_failure(ip: str, username: str) -> None:
    _record(_2fa_failed_by_ip, _2fa_failed_by_username, ip, username)


def clear_2fa_failures(ip: str, username: str) -> None:
    _clear(_2fa_failed_by_ip, _2fa_failed_by_username, ip, username)


def clear_2fa_user_failures(username: str) -> None:
    """Drop one user's wrong-code count (after a password change)."""
    _2fa_failed_by_username.pop(username, None)


def revoke_token(token: str) -> None:
    """Revoke an access token at logout (#146).

    Stores the token's jti in revoked_tokens until its exp, so the revocation
    survives restarts and deploys. Rows past their exp are pruned here. A
    token that doesn't verify, or has no string jti or numeric exp, is
    ignored: decode_token_claims refuses such tokens anyway.
    """
    from api.database import get_db
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM], options={"verify_exp": False})
    except jwt.PyJWTError:
        return
    jti, exp = payload.get("jti"), payload.get("exp")
    if not isinstance(jti, str) or not jti or not isinstance(exp, (int, float)):
        return
    with get_db() as conn:
        conn.execute("DELETE FROM revoked_tokens WHERE expires_at < ?", (int(time.time()),))
        conn.execute(
            "INSERT OR IGNORE INTO revoked_tokens (jti, expires_at) VALUES (?, ?)",
            (jti, int(exp)),
        )


def is_token_revoked(jti: str) -> bool:
    """True if the access token with this jti was revoked at logout (#146)."""
    from api.database import get_db
    with get_db() as conn:
        row = conn.execute("SELECT 1 FROM revoked_tokens WHERE jti = ?", (jti,)).fetchone()
    return row is not None


def validate_password_strength(password: str) -> str | None:
    """Return an error message if the password is too weak, or None if acceptable."""
    if len(password) < 8:
        return "Password must be at least 8 characters"
    if not re.search(r"[A-Z]", password):
        return "Password must contain at least one uppercase letter"
    if not re.search(r"[a-z]", password):
        return "Password must contain at least one lowercase letter"
    if not re.search(r"\d", password):
        return "Password must contain at least one digit"
    if len(password.encode()) > BCRYPT_MAX_BYTES:
        return "Password must be at most 72 bytes"
    return None


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


# bcrypt only uses the first 72 bytes. bcrypt 5 raises on longer input, which
# made a long password at /login a 500; older hashes were made from the first
# 72 bytes, so checking those bytes keeps every existing password working.
BCRYPT_MAX_BYTES = 72


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode()[:BCRYPT_MAX_BYTES], hashed.encode())


def authenticate_user(username: str, password: str) -> bool:
    from api.database import get_db
    with get_db() as conn:
        row = conn.execute(
            "SELECT password_hash, is_active FROM users WHERE username = ?",
            (username,)
        ).fetchone()
    # Only an active users row can log in. There is no AUTH_PASSWORD_HASH
    # fallback: seed_user_from_env creates the AUTH_USERNAME row on startup, and
    # get_current_user rejects tokens for users without an active row (issue #99).
    if row is None or row["is_active"] != 1:
        return False
    return verify_password(password, row["password_hash"])


def token_rejection_reason(username: str, issued_at: object) -> str | None:
    """Why a validly signed token must be refused for this user, or None if
    it is still good.

    The user needs an active users row (issue #99). When the row has a
    tokens_valid_after cutoff (Unix seconds, set on deactivation), the token's
    iat must be after it, so reactivating a user doesn't revive tokens issued
    before the deactivation (issue #126). NULL means no cutoff. Anything that
    isn't an integer on either side fails closed.
    """
    from api.database import get_db
    with get_db() as conn:
        row = conn.execute(
            "SELECT tokens_valid_after FROM users WHERE username = ? AND is_active = 1",
            (username,)
        ).fetchone()
    if row is None:
        return "inactive user"
    cutoff = row["tokens_valid_after"]
    if cutoff is None:
        return None
    if not isinstance(cutoff, int) or not isinstance(issued_at, int) or issued_at <= cutoff:
        return "token issued before cutoff"
    return None


def change_password_and_end_sessions(username: str, new_password: str) -> int:
    """Save a new password and end every session issued up to now (issue #132).

    In one transaction: store the new hash, move the user's tokens_valid_after
    cutoff to now (never lowering a later cutoff, like the #136 trigger), and
    revoke all their refresh tokens. Every access token, refresh token and 2FA
    pending token issued at or before the cutoff second is refused from then
    on. Returns the cutoff; a replacement token minted for the caller must
    have an iat after it.
    """
    from api.database import get_db
    new_hash = hash_password(new_password)
    with get_db() as conn:
        conn.execute(
            """UPDATE users
                  SET password_hash = ?,
                      tokens_valid_after = MAX(COALESCE(tokens_valid_after, 0), ?)
                WHERE username = ?""",
            (new_hash, int(time.time()), username),
        )
        conn.execute(
            "UPDATE refresh_tokens SET revoked = 1 WHERE username = ? AND revoked = 0",
            (username,),
        )
        row = conn.execute(
            "SELECT tokens_valid_after FROM users WHERE username = ?", (username,)
        ).fetchone()
    return row["tokens_valid_after"]


def get_user_2fa(username: str) -> dict | None:
    from api.database import get_db
    with get_db() as conn:
        row = conn.execute(
            "SELECT two_fa_enabled, totp_secret FROM users WHERE username = ? AND is_active = 1",
            (username,)
        ).fetchone()
    return dict(row) if row else None


# ── TOTP ──────────────────────────────────────────────────────────────────────

def generate_totp_setup(username: str) -> tuple[str, str]:
    """
    Generate a new TOTP secret, store it as pending (encrypted), and return
    (otpauth_uri, svg_qr_code_string).
    """
    from api.encryption import encrypt
    secret = pyotp.random_base32()
    totp = pyotp.TOTP(secret)
    uri = totp.provisioning_uri(name=username, issuer_name="Vaultic")

    # Store pending secret encrypted at rest
    from api.database import get_db
    with get_db() as conn:
        conn.execute(
            "UPDATE users SET totp_pending_secret = ? WHERE username = ?",
            (encrypt(secret), username)
        )

    # Generate SVG QR code using path factory (no Pillow, no namespace issues)
    img = qrcode.make(uri, image_factory=qrcode.image.svg.SvgPathImage)
    buf = io.BytesIO()
    img.save(buf)
    # Inject fill color so the path is visible
    svg = buf.getvalue().decode().replace("<path ", '<path fill="#000000" ')

    return uri, svg


def confirm_totp_enrollment(username: str, code: str) -> bool:
    """Verify the code against the pending secret and activate 2FA if correct."""
    from api.encryption import decrypt, encrypt
    from api.database import get_db
    with get_db() as conn:
        row = conn.execute(
            "SELECT totp_pending_secret FROM users WHERE username = ?", (username,)
        ).fetchone()
    if not row or not row["totp_pending_secret"]:
        return False
    # Decrypt the pending secret to verify the TOTP code
    pending_plain = decrypt(row["totp_pending_secret"])
    step = _matching_step(pyotp.TOTP(pending_plain), code)
    if step is None:
        return False
    # Store confirmed secret encrypted; clear pending. The enrollment code's
    # step counts as used, so it can't also finish a login.
    with get_db() as conn:
        conn.execute(
            "UPDATE users SET totp_secret = ?, totp_pending_secret = NULL, two_fa_enabled = 1, "
            "totp_last_step = ? WHERE username = ?",
            (encrypt(pending_plain), step, username)
        )
    return True


def _matching_step(totp: pyotp.TOTP, code: str) -> int | None:
    """The 30-second step whose code matches, checking the current step and
    one on each side (the same window as verify(valid_window=1)), or None."""
    now = int(time.time())
    for offset in (-1, 0, 1):
        at = now + offset * totp.interval
        if pyotp.utils.strings_equal(str(code), totp.at(at)):
            return at // totp.interval
    return None


TOTP_OK, TOTP_USED, TOTP_WRONG = "ok", "used", "wrong"


def verify_totp_code(username: str, code: str) -> str:
    """Check a TOTP code at login time: TOTP_OK, TOTP_USED or TOTP_WRONG.

    Each code works once (#165 review): the matching step must be later than
    the last one used, and recording it is a single conditional UPDATE, so two
    requests with the same code can't both pass. A right code whose step is
    already used is TOTP_USED, so the caller can say so instead of counting it
    as a wrong guess.
    """
    from api.encryption import decrypt
    from api.database import get_db
    with get_db() as conn:
        row = conn.execute(
            "SELECT totp_secret FROM users WHERE username = ? AND is_active = 1",
            (username,)
        ).fetchone()
    if not row or not row["totp_secret"]:
        return TOTP_WRONG
    # Decrypt the stored TOTP secret before verification
    secret_plain = decrypt(row["totp_secret"])
    step = _matching_step(pyotp.TOTP(secret_plain), code)
    if step is None:
        return TOTP_WRONG
    with get_db() as conn:
        claimed = conn.execute(
            "UPDATE users SET totp_last_step = ? WHERE username = ? AND is_active = 1 "
            "AND (totp_last_step IS NULL OR totp_last_step < ?)",
            (step, username, step),
        ).rowcount
    return TOTP_OK if claimed == 1 else TOTP_USED


# ── Standard auth ─────────────────────────────────────────────────────────────

def seed_user_from_env():
    username = os.environ.get("AUTH_USERNAME", "")
    password_hash = os.environ.get("AUTH_PASSWORD_HASH", "")
    if not username or not password_hash:
        return
    from api.database import get_db
    with get_db() as conn:
        existing = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
        if not existing:
            conn.execute(
                "INSERT INTO users (username, password_hash, is_admin) VALUES (?, ?, 1)",
                (username, password_hash)
            )


# ── Refresh token helpers ──────────────────────────────────────────────────────

REFRESH_TOKEN_EXPIRE_DAYS = 90  # Rolling: resets on every use


def _hash_token(raw: str) -> str:
    """SHA-256 hash a raw token string for safe DB storage."""
    return hashlib.sha256(raw.encode()).hexdigest()


def create_refresh_token(username: str) -> str:
    """Issue a new refresh token, store its hash in the DB, return the raw value.

    The raw token is URL-safe base64 (48 bytes = 64 chars) — never stored in DB.
    Only the SHA-256 hash is persisted so a DB leak can't be weaponised directly.
    """
    from api.database import get_db
    raw = secrets.token_urlsafe(48)
    expires_at = (
        datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    ).isoformat()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO refresh_tokens (token_hash, username, expires_at) VALUES (?, ?, ?)",
            (_hash_token(raw), username, expires_at),
        )
    return raw


def validate_refresh_token(raw: str) -> str | None:
    """Return the username if the refresh token is valid and unexpired, else None."""
    from api.database import get_db
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        # The JOIN means a deactivated user's refresh token stops working even if
        # it was somehow left unrevoked (issue #99).
        row = conn.execute(
            """SELECT rt.username,
                      CAST(strftime('%s', rt.created_at) AS INTEGER) AS created_ts
               FROM refresh_tokens rt
               JOIN users u ON u.username = rt.username AND u.is_active = 1
               WHERE rt.token_hash = ? AND rt.revoked = 0 AND rt.expires_at > ?""",
            (_hash_token(raw), now),
        ).fetchone()
    if row is None:
        return None
    # A refresh token created before the user's cutoff is refused the same way
    # an access token issued before it is, even if it was left unrevoked (#136).
    if token_rejection_reason(row["username"], row["created_ts"]):
        return None
    return row["username"]


def rotate_refresh_token(old_raw: str, username: str) -> str:
    """Revoke the old refresh token and issue a fresh one (rolling 90-day window)."""
    from api.database import get_db
    with get_db() as conn:
        conn.execute(
            "UPDATE refresh_tokens SET revoked = 1 WHERE token_hash = ?",
            (_hash_token(old_raw),),
        )
    return create_refresh_token(username)


def revoke_refresh_token(raw: str):
    """Mark a refresh token as revoked — logout kills the session immediately."""
    from api.database import get_db
    with get_db() as conn:
        conn.execute(
            "UPDATE refresh_tokens SET revoked = 1 WHERE token_hash = ?",
            (_hash_token(raw),),
        )


# ── Access token helpers ───────────────────────────────────────────────────────

def create_token(username: str, hours: int | None = None) -> str:
    """Issue a signed access token.

    hours overrides TOKEN_EXPIRE_HOURS:
      - Web (no remember_me): 720 h (30 days) — passed explicitly by the router
      - Mobile (remember_me):   1 h — short-lived; paired with a refresh token
    """
    now = datetime.now(timezone.utc)
    exp = now + timedelta(hours=hours if hours is not None else TOKEN_EXPIRE_HOURS)
    payload = {"sub": username, "exp": exp, "iat": now, "jti": str(uuid.uuid4())}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def create_2fa_pending_token(username: str) -> str:
    """Short-lived token proving user passed password auth, scoped to 2FA verification only."""
    now = datetime.now(timezone.utc)
    payload = {"sub": username, "exp": now + timedelta(minutes=5), "iat": now,
               "purpose": "2fa_pending"}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_2fa_pending_token(token: str) -> str | None:
    """Decode a 2FA pending token. Returns username if valid, None otherwise.

    The token proves the password step passed, so one issued at or before the
    user's cutoff (a password change, issue #132, or a deactivation) is refused:
    a login that used the old password can't finish 2FA after the change.
    """
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        return None
    if payload.get("purpose") != "2fa_pending":
        return None
    username = payload.get("sub")
    if not isinstance(username, str) or token_rejection_reason(username, payload.get("iat")):
        return None
    return username


def decode_token_claims(token: str) -> dict | None:
    """Return a general-access token's claims, or None if it is invalid,
    expired, revoked, or scoped (e.g. 2fa_pending).

    exp, iat, sub and jti are required: create_token sets all four, and the
    jti is what logout revokes (#146).
    """
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM],
                             options={"require": ["exp", "iat", "sub", "jti"]})
    except jwt.PyJWTError:
        return None
    if payload.get("purpose"):
        return None  # Reject scoped tokens (e.g. 2fa_pending) from general auth
    jti = payload["jti"]
    if not isinstance(jti, str) or not jti or is_token_revoked(jti):
        return None
    return payload


def decode_token(token: str) -> str | None:
    claims = decode_token_claims(token)
    return claims.get("sub") if claims else None
