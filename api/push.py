"""Web Push notification utilities.

Implements VAPID (Voluntary Application Server Identification) authentication
and RFC 8291 message encryption (content-encoding: aes128gcm) so the server
can send push notifications directly to subscribed browsers without a
third-party push SDK.

All crypto uses the `cryptography` library (already installed as a FastAPI
dependency). JWT signing uses PyJWT (also already installed).

Required environment variables (generate with scripts/generate_vapid_keys.py):
  VAPID_PRIVATE_KEY_PEM — EC private key in PEM format
  VAPID_PUBLIC_KEY      — base64url-encoded uncompressed P-256 point (65 bytes)
  VAPID_EMAIL           — admin contact email (e.g. mailto:you@example.com)
"""

import base64
import json
import logging
import os
import struct
import time
from urllib.parse import urlparse

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.serialization import (
    Encoding, PublicFormat, load_pem_private_key,
)

logger = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _b64d(s: str) -> bytes:
    """Decode a base64url string (padding-tolerant)."""
    s = s.replace("-", "+").replace("_", "/")
    s += "=" * (4 - len(s) % 4)
    return base64.b64decode(s)


def _load_private_key():
    """Load the VAPID EC private key from VAPID_PRIVATE_KEY_PEM env var.

    The .env file stores the PEM as a single line with literal \\n separating
    the header, base64 body, and footer.  We decode those back to real newlines
    before handing the bytes to load_pem_private_key(), which requires a
    properly formatted PEM with actual line breaks.
    """
    pem = os.environ.get("VAPID_PRIVATE_KEY_PEM", "")
    if not pem:
        raise RuntimeError("VAPID_PRIVATE_KEY_PEM not configured — run scripts/generate_vapid_keys.py")
    # Decode literal \n (stored that way in .env) to real newlines
    pem = pem.replace("\\n", "\n")
    return load_pem_private_key(pem.encode(), password=None)


def get_vapid_public_key() -> str:
    """Return the base64url-encoded VAPID public key for browser subscription."""
    key = os.environ.get("VAPID_PUBLIC_KEY", "")
    if not key:
        raise RuntimeError("VAPID_PUBLIC_KEY not configured — run scripts/generate_vapid_keys.py")
    return key


def is_configured() -> bool:
    """Return True if VAPID keys are present in the environment."""
    return bool(os.environ.get("VAPID_PRIVATE_KEY_PEM") and os.environ.get("VAPID_PUBLIC_KEY"))


# ── VAPID JWT ─────────────────────────────────────────────────────────────────

def _create_vapid_jwt(endpoint: str) -> str:
    """Create a signed VAPID JWT for the push service at the given endpoint.

    The JWT is signed with ES256 (ECDSA P-256 + SHA-256) and contains:
      aud — the push service origin (https://fcm.googleapis.com, etc.)
      exp — expiry 12 hours from now
      sub — admin contact (mailto: URI from VAPID_EMAIL env var)
    """
    parsed = urlparse(endpoint)
    audience = f"{parsed.scheme}://{parsed.netloc}"
    email = os.environ.get("VAPID_EMAIL", "mailto:jason.bazen@yahoo.com")

    claims = {
        "aud": audience,
        "exp": int(time.time()) + 43_200,  # 12 hours
        "sub": email,  # mailto:jason.bazen@yahoo.com (set via VAPID_EMAIL in .env)
    }

    private_key = _load_private_key()
    return jwt.encode(claims, private_key, algorithm="ES256")


# ── RFC 8291 message encryption ───────────────────────────────────────────────

def _encrypt_payload(subscription: dict, plaintext: str) -> bytes:
    """Encrypt a plaintext message for delivery to the given push subscription.

    Follows RFC 8291 (Web Push Message Encryption):
      1. Parse receiver's P-256 public key and 16-byte auth secret from sub
      2. Generate an ephemeral ECDH key pair (new per message for forward secrecy)
      3. Derive CEK and NONCE from the shared secret + auth using HKDF
      4. Encrypt with AES-128-GCM and prepend the aes128gcm binary header

    The binary output is sent as the HTTP body with:
      Content-Type: application/octet-stream
      Content-Encoding: aes128gcm
    """
    keys = subscription.get("keys", {})
    receiver_pub_bytes = _b64d(keys["p256dh"])    # uncompressed P-256 point (65 B)
    auth_secret        = _b64d(keys["auth"])       # 16-byte random auth secret

    # Load receiver public key
    receiver_pub = ec.EllipticCurvePublicKey.from_encoded_point(
        ec.SECP256R1(), receiver_pub_bytes
    )

    # Ephemeral sender key pair — new per message for forward secrecy
    sender_priv     = ec.generate_private_key(ec.SECP256R1())
    sender_pub_bytes = sender_priv.public_key().public_bytes(
        Encoding.X962, PublicFormat.UncompressedPoint
    )  # 65 bytes (0x04 || X || Y)

    # ECDH: compute raw shared secret (X coordinate of the shared point)
    shared_secret = sender_priv.exchange(ec.ECDH(), receiver_pub)

    # Random 16-byte salt
    salt = os.urandom(16)

    # --- Key derivation per RFC 8291 Section 3.3 ---
    #
    # Step 1: PRK = HKDF(salt=auth_secret, IKM=shared_secret,
    #                    info="WebPush: info\x00" || receiver_pub || sender_pub,
    #                    length=32)
    #
    # This mixes the ECDH secret with both parties' public keys and the
    # subscription's auth secret to prevent cross-subscription attacks.
    ikm_info = b"WebPush: info\x00" + receiver_pub_bytes + sender_pub_bytes
    prk = HKDF(
        algorithm=SHA256(), length=32, salt=auth_secret, info=ikm_info,
    ).derive(shared_secret)

    # Step 2: CEK = HKDF(salt=salt, IKM=PRK,
    #                    info="Content-Encoding: aes128gcm\x00", length=16)
    cek = HKDF(
        algorithm=SHA256(), length=16, salt=salt,
        info=b"Content-Encoding: aes128gcm\x00",
    ).derive(prk)

    # Step 3: NONCE = HKDF(salt=salt, IKM=PRK,
    #                      info="Content-Encoding: nonce\x00", length=12)
    nonce = HKDF(
        algorithm=SHA256(), length=12, salt=salt,
        info=b"Content-Encoding: nonce\x00",
    ).derive(prk)

    # Encrypt: append 0x02 end-of-record delimiter, then AES-128-GCM (no AAD)
    padded     = plaintext.encode("utf-8") + b"\x02"
    ciphertext = AESGCM(cek).encrypt(nonce, padded, None)

    # aes128gcm binary header: salt(16) + rs(uint32 BE) + idlen(1) + key_id
    record_size = 4096  # one record is enough for small notification payloads
    header = (
        salt
        + struct.pack(">I", record_size)
        + bytes([len(sender_pub_bytes)])
        + sender_pub_bytes
    )

    return header + ciphertext


# ── Public send function ──────────────────────────────────────────────────────

def send_push_notification(subscription: dict, title: str, body: str,
                           url: str = "/budget") -> bool:
    """Send one encrypted Web Push notification to a subscribed browser.

    Uses synchronous httpx so this can be called from the APScheduler sync
    job (sync_all) without needing to bridge into the async event loop.

    Args:
        subscription: Browser PushSubscription object
                      {endpoint, keys: {p256dh, auth}}
        title:        OS notification title
        body:         OS notification body text
        url:          URL to open when the user taps the notification

    Returns:
        True  — push service accepted the message (2xx)
        False — push service rejected (subscription expired, etc.)
    """
    if not is_configured():
        logger.warning("VAPID keys not configured — skipping push notification")
        return False

    payload = json.dumps({"title": title, "body": body, "url": url})

    try:
        encrypted = _encrypt_payload(subscription, payload)
        token     = _create_vapid_jwt(subscription["endpoint"])
        pub_key   = get_vapid_public_key()

        with httpx.Client(timeout=10) as client:
            resp = client.post(
                subscription["endpoint"],
                content=encrypted,
                headers={
                    # VAPID auth: signed JWT + base64url public key
                    "Authorization": f"vapid t={token},k={pub_key}",
                    "Content-Type": "application/octet-stream",
                    "Content-Encoding": "aes128gcm",
                    "TTL": "86400",  # push service queues message for up to 24 h
                },
            )

        if resp.status_code in (200, 201, 202):
            return True

        if resp.status_code == 410:
            # 410 Gone — subscription revoked by browser or push service.
            # Caller should mark it inactive so we stop sending to it.
            logger.info("Push subscription expired (410) — will deactivate")
            return False

        logger.warning(f"Push returned HTTP {resp.status_code}: {resp.text[:300]}")
        return False

    except Exception as exc:
        logger.error(f"Push send error: {exc}")
        return False


def notify_pending_review(count: int) -> None:
    """Fan-out a 'transactions pending review' push to all active subscribers.

    Called by sync_all() after _auto_categorize_new() so the user gets an
    immediate notification when new transactions are ready to approve.

    Subscriptions that return 410 (expired) are deactivated automatically so
    we don't keep sending to dead endpoints.

    Args:
        count: Number of newly created pending_review assignments in this sync.
    """
    if count <= 0 or not is_configured():
        return

    from api.database import get_db

    noun  = "transaction" if count == 1 else "transactions"
    verb  = "needs" if count == 1 else "need"
    title = "Vaultic — Review Needed"
    body  = f"{count} {noun} {verb} your approval"

    with get_db() as conn:
        subs = conn.execute(
            "SELECT id, endpoint, p256dh, auth FROM push_subscriptions WHERE is_active = 1"
        ).fetchall()

    expired_ids = []
    for sub in subs:
        subscription = {
            "endpoint": sub["endpoint"],
            "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]},
        }
        ok = send_push_notification(subscription, title, body, url="/budget")
        if not ok:
            expired_ids.append(sub["id"])

    if expired_ids:
        with get_db() as conn:
            for sid in expired_ids:
                conn.execute(
                    "UPDATE push_subscriptions SET is_active = 0 WHERE id = ?", (sid,)
                )
        logger.info(f"Deactivated {len(expired_ids)} expired push subscription(s)")
