"""Push notification subscription endpoints.

Endpoints:
  GET  /api/push/vapid-public-key  — return base64url VAPID public key (no auth)
  POST /api/push/subscribe         — store a new browser push subscription
  POST /api/push/unsubscribe       — deactivate a subscription by endpoint

The VAPID public key endpoint is intentionally unauthenticated because the
browser needs it before the user has logged in (to call PushManager.subscribe).
The public key is not secret — it just identifies this server to push services.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.database import get_db
from api.dependencies import get_current_user
from api.push import get_vapid_public_key, is_configured

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

    with get_db() as conn:
        # Upsert: insert new or reactivate existing endpoint
        conn.execute(
            """INSERT INTO push_subscriptions (endpoint, p256dh, auth, is_active)
               VALUES (?, ?, ?, 1)
               ON CONFLICT(endpoint) DO UPDATE SET
                 p256dh    = excluded.p256dh,
                 auth      = excluded.auth,
                 is_active = 1""",
            (body.endpoint, body.p256dh, body.auth),
        )

    return {"status": "subscribed"}


@router.post("/unsubscribe")
async def unsubscribe(body: UnsubscribeBody, _user: str = Depends(get_current_user)):
    """Deactivate a push subscription (soft-delete keeps the row for audit)."""
    with get_db() as conn:
        conn.execute(
            "UPDATE push_subscriptions SET is_active = 0 WHERE endpoint = ?",
            (body.endpoint,),
        )
    return {"status": "unsubscribed"}
