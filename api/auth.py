import os
import io
import time
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
MAX_ATTEMPTS = 5
LOCKOUT_WINDOW = 15 * 60

# Token denylist: stores revoked tokens until they would naturally expire.
# Key = token string, value = expiry timestamp. Cleaned up on each check.
_token_denylist: dict[str, float] = {}


def is_rate_limited(ip: str) -> bool:
    now = time.time()
    _failed_attempts[ip] = [t for t in _failed_attempts[ip] if now - t < LOCKOUT_WINDOW]
    return len(_failed_attempts[ip]) >= MAX_ATTEMPTS


def record_failed_attempt(ip: str):
    _failed_attempts[ip].append(time.time())


def clear_failed_attempts(ip: str):
    _failed_attempts.pop(ip, None)


def revoke_token(token: str):
    """Add a token to the denylist. It will be cleaned up after its natural expiry."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM], options={"verify_exp": False})
        exp = payload.get("exp", 0)
        _token_denylist[token] = exp
    except jwt.PyJWTError:
        pass  # Invalid token, nothing to revoke


def is_token_revoked(token: str) -> bool:
    """Check if a token has been revoked. Also cleans up expired entries."""
    now = datetime.now(timezone.utc).timestamp()
    # Clean up expired entries
    expired = [t for t, exp in _token_denylist.items() if exp < now]
    for t in expired:
        _token_denylist.pop(t, None)
    return token in _token_denylist


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def authenticate_user(username: str, password: str) -> bool:
    from api.database import get_db
    with get_db() as conn:
        row = conn.execute(
            "SELECT password_hash FROM users WHERE username = ? AND is_active = 1",
            (username,)
        ).fetchone()
    if not row:
        expected_username = os.environ.get("AUTH_USERNAME", "")
        expected_hash = os.environ.get("AUTH_PASSWORD_HASH", "")
        if username != expected_username or not expected_hash:
            return False
        return verify_password(password, expected_hash)
    return verify_password(password, row["password_hash"])


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
    totp = pyotp.TOTP(pending_plain)
    if not totp.verify(code, valid_window=1):
        return False
    # Store confirmed secret encrypted; clear pending
    with get_db() as conn:
        conn.execute(
            "UPDATE users SET totp_secret = ?, totp_pending_secret = NULL, two_fa_enabled = 1 WHERE username = ?",
            (encrypt(pending_plain), username)
        )
    return True


def verify_totp_code(username: str, code: str) -> bool:
    """Verify a TOTP code at login time."""
    from api.encryption import decrypt
    from api.database import get_db
    with get_db() as conn:
        row = conn.execute(
            "SELECT totp_secret FROM users WHERE username = ? AND is_active = 1",
            (username,)
        ).fetchone()
    if not row or not row["totp_secret"]:
        return False
    # Decrypt the stored TOTP secret before verification
    secret_plain = decrypt(row["totp_secret"])
    return pyotp.TOTP(secret_plain).verify(code, valid_window=1)


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


def create_token(username: str) -> str:
    exp = datetime.now(timezone.utc) + timedelta(hours=TOKEN_EXPIRE_HOURS)
    payload = {"sub": username, "exp": exp}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def create_2fa_pending_token(username: str) -> str:
    """Short-lived token proving user passed password auth, scoped to 2FA verification only."""
    exp = datetime.now(timezone.utc) + timedelta(minutes=5)
    payload = {"sub": username, "exp": exp, "purpose": "2fa_pending"}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_2fa_pending_token(token: str) -> str | None:
    """Decode a 2FA pending token. Returns username if valid, None otherwise."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("purpose") != "2fa_pending":
            return None
        return payload.get("sub")
    except jwt.PyJWTError:
        return None


def decode_token(token: str) -> str | None:
    try:
        if is_token_revoked(token):
            return None
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("purpose"):
            return None  # Reject scoped tokens (e.g. 2fa_pending) from general auth
        return payload.get("sub")
    except jwt.PyJWTError:
        return None
