"""Push notification subscription endpoints.

Endpoints:
  GET  /api/push/vapid-public-key  — return base64url VAPID public key (no auth)
  POST /api/push/subscribe         — store a new browser push subscription
  POST /api/push/unsubscribe       — deactivate a subscription by endpoint

The VAPID public key endpoint is intentionally unauthenticated because the
browser needs it before the user has logged in (to call PushManager.subscribe).
The public key is not secret — it just identifies this server to push services.
"""

import secrets

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.auth import create_token
from api.database import get_db
from api.dependencies import get_current_user
from api.push import get_vapid_public_key, is_configured, send_push_notification

router = APIRouter(prefix="/api/push", tags=["push"])


# ── Request models ─────────────────────────────────────────────────────────────

class SubscribeBody(BaseModel):
    """Browser PushSubscription object forwarded from the frontend.

    The browser's PushManager.subscribe() returns this object; the frontend
    passes it directly to POST /subscribe so the server can send notifications.

    Fields:
        endpoint — push service URL (e.g. https://fcm.googleapis.com/...)
        p256dh   — base64url-encoded receiver public key (P-256, uncompressed)
        auth     — base64url-encoded 16-byte authentication secret
    """
    endpoint: str
    p256dh: str
    auth: str


class UnsubscribeBody(BaseModel):
    endpoint: str


class DeviceAuthBody(BaseModel):
    device_token: str


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/vapid-public-key")
async def get_public_key():
    """Return the VAPID application server public key.

    The frontend uses this as the `applicationServerKey` when calling
    PushManager.subscribe(), linking push subscriptions to this server.

    This endpoint is intentionally public (no JWT required) because the key
    is not sensitive and is needed before authentication is complete.
    """
    if not is_configured():
        raise HTTPException(
            status_code=503,
            detail="Push notifications not configured on this server"
        )
    return {"publicKey": get_vapid_public_key()}


@router.post("/subscribe")
async def subscribe(body: SubscribeBody, _user: str = Depends(get_current_user)):
    """Store or reactivate a push subscription.

    If the endpoint already exists, reactivate it (in case it was deactivated
    after a 410 and the user re-subscribed with the same endpoint).
    """
    if not is_configured():
        raise HTTPException(
            status_code=503,
            detail="Push notifications not configured on this server"
        )

    # Generate a long-lived device token so this device can auto-authenticate
    # on the Review page without requiring a manual login every 24 hours.
    # The token is returned once and stored by the frontend in localStorage.
    device_token = secrets.token_hex(32)

    with get_db() as conn:
        # Upsert: insert new or reactivate existing endpoint, refreshing device_token
        conn.execute(
            """INSERT INTO push_subscriptions (endpoint, p256dh, auth, device_token, username, is_active)
               VALUES (?, ?, ?, ?, ?, 1)
               ON CONFLICT(endpoint) DO UPDATE SET
                 p256dh       = excluded.p256dh,
                 auth         = excluded.auth,
                 device_token = excluded.device_token,
                 username     = excluded.username,
                 is_active    = 1""",
            (body.endpoint, body.p256dh, body.auth, device_token, _user),
        )

    # Return the device_token so the frontend can store it for auto-auth
    return {"status": "subscribed", "device_token": device_token}


@router.post("/device-auth")
async def device_auth(body: DeviceAuthBody):
    """Exchange a stored device_token for a fresh JWT — no password required.

    This endpoint is intentionally unauthenticated so that the Review page
    can silently re-authenticate when the user taps a push notification, even
    after their normal 24-hour JWT has expired.

    Security model: the device_token is a 64-character random hex string
    generated at subscribe time and stored only in the browser's localStorage
    and the server's push_subscriptions table. Only the subscribed device
    can use it. Tokens rotate on each new subscribe() call.
    """
    if not body.device_token:
        raise HTTPException(status_code=400, detail="device_token required")

    with get_db() as conn:
        row = conn.execute(
            """SELECT username FROM push_subscriptions
               WHERE device_token = ? AND is_active = 1""",
            (body.device_token,),
        ).fetchone()

    if not row:
        raise HTTPException(status_code=401, detail="Invalid or expired device token")

    # Issue a fresh JWT for the user who subscribed this device
    import os
    username = row["username"] or os.environ.get("AUTH_USERNAME", "jbazen")
    token = create_token(username)
    return {"token": token}


@router.post("/unsubscribe")
async def unsubscribe(body: UnsubscribeBody, _user: str = Depends(get_current_user)):
    """Deactivate a push subscription (soft-delete keeps the row for audit)."""
    with get_db() as conn:
        conn.execute(
            "UPDATE push_subscriptions SET is_active = 0 WHERE endpoint = ?",
            (body.endpoint,),
        )
    return {"status": "unsubscribed"}


@router.post("/test")
async def send_test_notification(_user: str = Depends(get_current_user)):
    """Send a test push notification to all active subscriptions for this user.

    Fires a sample notification so the user can verify their device is set up
    correctly without waiting for a real sync to produce pending transactions.
    """
    if not is_configured():
        raise HTTPException(
            status_code=503,
            detail="Push notifications not configured on this server"
        )

    with get_db() as conn:
        subs = conn.execute(
            "SELECT id, endpoint, p256dh, auth FROM push_subscriptions WHERE is_active = 1"
        ).fetchall()

    if not subs:
        raise HTTPException(status_code=404, detail="No active push subscriptions found")

    expired_ids = []
    sent = 0
    for sub in subs:
        subscription = {
            "endpoint": sub["endpoint"],
            "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]},
        }
        ok = send_push_notification(
            subscription,
            title="Vaultic — Test Notification",
            body="Push notifications are working correctly.",
            url="/review",
        )
        if ok:
            sent += 1
        else:
            expired_ids.append(sub["id"])

    # Deactivate any subscriptions that returned 410 Gone
    if expired_ids:
        with get_db() as conn:
            for sid in expired_ids:
                conn.execute(
                    "UPDATE push_subscriptions SET is_active = 0 WHERE id = ?", (sid,)
                )

    return {"sent": sent, "expired": len(expired_ids)}
