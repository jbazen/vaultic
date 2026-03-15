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
ALGORITHM = "HS256"
TOKEN_EXPIRE_HOURS = int(os.environ.get("JWT_EXPIRE_HOURS", "24"))

_failed_attempts: dict[str, list[float]] = defaultdict(list)
MAX_ATTEMPTS = 5
LOCKOUT_WINDOW = 15 * 60


def is_rate_limited(ip: str) -> bool:
    now = time.time()
    _failed_attempts[ip] = [t for t in _failed_attempts[ip] if now - t < LOCKOUT_WINDOW]
    return len(_failed_attempts[ip]) >= MAX_ATTEMPTS


def record_failed_attempt(ip: str):
    _failed_attempts[ip].append(time.time())


def clear_failed_attempts(ip: str):
    _failed_attempts.pop(ip, None)


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
    Generate a new TOTP secret, store it as pending, and return
    (otpauth_uri, svg_qr_code_string).
    """
    secret = pyotp.random_base32()
    totp = pyotp.TOTP(secret)
    uri = totp.provisioning_uri(name=username, issuer_name="Vaultic")

    # Store pending (not yet confirmed)
    from api.database import get_db
    with get_db() as conn:
        conn.execute(
            "UPDATE users SET totp_pending_secret = ? WHERE username = ?",
            (secret, username)
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
    from api.database import get_db
    with get_db() as conn:
        row = conn.execute(
            "SELECT totp_pending_secret FROM users WHERE username = ?", (username,)
        ).fetchone()
    if not row or not row["totp_pending_secret"]:
        return False
    totp = pyotp.TOTP(row["totp_pending_secret"])
    if not totp.verify(code, valid_window=1):
        return False
    with get_db() as conn:
        conn.execute(
            "UPDATE users SET totp_secret = totp_pending_secret, totp_pending_secret = NULL, two_fa_enabled = 1 WHERE username = ?",
            (username,)
        )
    return True


def verify_totp_code(username: str, code: str) -> bool:
    """Verify a TOTP code at login time."""
    from api.database import get_db
    with get_db() as conn:
        row = conn.execute(
            "SELECT totp_secret FROM users WHERE username = ? AND is_active = 1",
            (username,)
        ).fetchone()
    if not row or not row["totp_secret"]:
        return False
    return pyotp.TOTP(row["totp_secret"]).verify(code, valid_window=1)


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
                "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                (username, password_hash)
            )


def create_token(username: str) -> str:
    exp = datetime.now(timezone.utc) + timedelta(hours=TOKEN_EXPIRE_HOURS)
    payload = {"sub": username, "exp": exp}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> str | None:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload.get("sub")
    except jwt.PyJWTError:
        return None
