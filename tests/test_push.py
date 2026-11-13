"""Tests for the push notification router (api/routers/push.py).

The in-memory test DB is shared across the whole session, so every test uses
its own endpoint URL and only asserts on rows it created.
"""

import uuid
from unittest.mock import patch

import pytest

import api.routers.push as push_router
from api.database import get_db


def _endpoint() -> str:
    """Return a unique push endpoint URL so tests never share a row."""
    return f"https://push.example.com/test-push/{uuid.uuid4().hex}"


def _subscribe_body(endpoint: str, p256dh: str = "p256dh_key", auth: str = "auth_secret") -> dict:
    return {"endpoint": endpoint, "p256dh": p256dh, "auth": auth}


def _row(endpoint: str):
    with get_db() as conn:
        return conn.execute(
            "SELECT * FROM push_subscriptions WHERE endpoint = ?", (endpoint,)
        ).fetchone()


@pytest.fixture
def push_configured():
    """Pretend VAPID keys are configured so the endpoints don't return 503."""
    with patch.object(push_router, "is_configured", return_value=True), \
         patch.object(push_router, "get_vapid_public_key", return_value="test-public-key"):
        yield


@pytest.fixture
def only_subscription(push_configured):
    """Deactivate every existing subscription, then yield a helper that adds one.

    /api/push/test sends to every active subscription, so tests that count
    sends must start from a known set.
    """
    with get_db() as conn:
        conn.execute("UPDATE push_subscriptions SET is_active = 0")

    def _add() -> str:
        endpoint = _endpoint()
        with get_db() as conn:
            conn.execute(
                "INSERT INTO push_subscriptions (endpoint, p256dh, auth, username, is_active) "
                "VALUES (?, 'p256dh_key', 'auth_secret', 'testuser', 1)",
                (endpoint,),
            )
        return endpoint

    return _add


class TestPushAuthGuards:
    """Mutating push endpoints require a valid JWT."""

    @pytest.mark.parametrize("path,body", [
        ("/api/push/subscribe", {"endpoint": "https://push.example.com/x", "p256dh": "k", "auth": "a"}),
        ("/api/push/unsubscribe", {"endpoint": "https://push.example.com/x"}),
        ("/api/push/test", None),
    ])
    def test_requires_token(self, client, push_configured, path, body):
        resp = client.post(path, json=body)
        assert resp.status_code == 401
        assert resp.headers.get("www-authenticate") == "Bearer"

    def test_rejects_invalid_token(self, client, push_configured):
        resp = client.post(
            "/api/push/subscribe",
            json=_subscribe_body(_endpoint()),
            headers={"Authorization": "Bearer not-a-real-token"},
        )
        assert resp.status_code == 401
        assert resp.headers.get("www-authenticate") == "Bearer"


class TestDeviceAuthRemoved:
    """Regression for #85 and #66: the passwordless device-token login is gone."""

    @pytest.mark.parametrize("body", [{"device_token": "a" * 64}, None])
    def test_device_auth_returns_404(self, client, body):
        resp = client.post("/api/push/device-auth", json=body)
        assert resp.status_code == 404
        assert "token" not in resp.json()


class TestVapidPublicKey:
    """The VAPID public key is intentionally public."""

    def test_public_without_auth(self, client, push_configured):
        resp = client.get("/api/push/vapid-public-key")
        assert resp.status_code == 200
        assert resp.json() == {"publicKey": "test-public-key"}

    def test_503_when_not_configured(self, client):
        with patch.object(push_router, "is_configured", return_value=False):
            resp = client.get("/api/push/vapid-public-key")
        assert resp.status_code == 503


class TestSubscribe:

    def test_stores_subscription_for_user(self, client, auth_headers, push_configured):
        endpoint = _endpoint()
        resp = client.post("/api/push/subscribe", json=_subscribe_body(endpoint), headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["status"] == "subscribed"

        row = _row(endpoint)
        assert row is not None
        assert row["username"] == "testuser"
        assert row["is_active"] == 1
        assert row["p256dh"] == "p256dh_key"
        assert row["auth"] == "auth_secret"

    def test_reactivates_and_updates_existing_endpoint(self, client, auth_headers, push_configured):
        endpoint = _endpoint()
        client.post("/api/push/subscribe", json=_subscribe_body(endpoint), headers=auth_headers)
        with get_db() as conn:
            conn.execute("UPDATE push_subscriptions SET is_active = 0 WHERE endpoint = ?", (endpoint,))

        resp = client.post(
            "/api/push/subscribe",
            json=_subscribe_body(endpoint, p256dh="new_key", auth="new_auth"),
            headers=auth_headers,
        )
        assert resp.status_code == 200

        row = _row(endpoint)
        assert row["is_active"] == 1
        assert row["p256dh"] == "new_key"
        assert row["auth"] == "new_auth"
        with get_db() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM push_subscriptions WHERE endpoint = ?", (endpoint,)
            ).fetchone()[0]
        assert count == 1

    def test_503_when_not_configured(self, client, auth_headers):
        with patch.object(push_router, "is_configured", return_value=False):
            resp = client.post("/api/push/subscribe", json=_subscribe_body(_endpoint()), headers=auth_headers)
        assert resp.status_code == 503


class TestUnsubscribe:

    def test_deactivates_subscription(self, client, auth_headers, push_configured):
        endpoint = _endpoint()
        client.post("/api/push/subscribe", json=_subscribe_body(endpoint), headers=auth_headers)

        resp = client.post("/api/push/unsubscribe", json={"endpoint": endpoint}, headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == {"status": "unsubscribed"}
        assert _row(endpoint)["is_active"] == 0


class TestSendTest:

    def test_sends_to_active_subscription(self, client, auth_headers, only_subscription):
        only_subscription()
        with patch.object(push_router, "send_push_notification", return_value=True) as mock_send:
            resp = client.post("/api/push/test", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == {"sent": 1, "expired": 0}
        mock_send.assert_called_once()

    def test_failed_send_deactivates_subscription(self, client, auth_headers, only_subscription):
        endpoint = only_subscription()
        with patch.object(push_router, "send_push_notification", return_value=False):
            resp = client.post("/api/push/test", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == {"sent": 0, "expired": 1}
        assert _row(endpoint)["is_active"] == 0

    def test_404_when_no_active_subscriptions(self, client, auth_headers, only_subscription):
        with patch.object(push_router, "send_push_notification") as mock_send:
            resp = client.post("/api/push/test", headers=auth_headers)
        assert resp.status_code == 404
        mock_send.assert_not_called()
