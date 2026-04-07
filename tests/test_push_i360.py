"""Tests for notify_stale_i360 push reminder logic."""

from datetime import datetime, timedelta
from unittest.mock import patch

import api.push as push_module
from api.database import get_db


class TestNotifyStaleI360:
    """Verify the once-per-day stale I360 push reminder."""

    def setup_method(self):
        # Reset the daily guard before each test
        push_module._last_i360_stale_notify_date = None

    def test_skips_when_no_sync_log(self):
        """No i360_sync_log rows → no notification (I360 not configured)."""
        with patch.object(push_module, "is_configured", return_value=True), \
             patch.object(push_module, "send_push_notification") as mock_send:
            push_module.notify_stale_i360()
            mock_send.assert_not_called()

    def test_skips_when_fresh(self):
        """Sync within 24h → no notification."""
        now = datetime.now()
        with get_db() as conn:
            conn.execute(
                "INSERT INTO i360_sync_log (synced_at, status) VALUES (?, 'success')",
                (now.isoformat(),)
            )

        with patch.object(push_module, "is_configured", return_value=True), \
             patch.object(push_module, "send_push_notification") as mock_send:
            push_module.notify_stale_i360()
            mock_send.assert_not_called()

    def test_sends_when_stale(self):
        """Sync >24h ago → sends notification."""
        stale_time = (datetime.now() - timedelta(hours=30)).isoformat()
        with get_db() as conn:
            conn.execute("DELETE FROM i360_sync_log")
            conn.execute(
                "INSERT INTO i360_sync_log (synced_at, status) VALUES (?, 'success')",
                (stale_time,)
            )
            conn.execute(
                "INSERT INTO push_subscriptions (endpoint, p256dh, auth, username, is_active) "
                "VALUES ('https://push.example.com/sub1', 'fake_key', 'fake_auth', 'testuser', 1)"
            )

        with patch.object(push_module, "is_configured", return_value=True), \
             patch.object(push_module, "send_push_notification", return_value=True) as mock_send:
            push_module.notify_stale_i360()
            mock_send.assert_called_once()
            title = mock_send.call_args.args[1]
            assert "Parker" in title

    def test_once_per_day_guard(self):
        """Second call on the same day should not send again."""
        stale_time = (datetime.now() - timedelta(hours=30)).isoformat()
        with get_db() as conn:
            conn.execute("DELETE FROM i360_sync_log")
            conn.execute(
                "INSERT INTO i360_sync_log (synced_at, status) VALUES (?, 'success')",
                (stale_time,)
            )

        with patch.object(push_module, "is_configured", return_value=True), \
             patch.object(push_module, "send_push_notification", return_value=True) as mock_send:
            push_module.notify_stale_i360()
            push_module.notify_stale_i360()  # second call same day
            assert mock_send.call_count == 1

    def test_skips_when_vapid_not_configured(self):
        """No VAPID keys → no notification, no error."""
        with patch.object(push_module, "is_configured", return_value=False), \
             patch.object(push_module, "send_push_notification") as mock_send:
            push_module.notify_stale_i360()
            mock_send.assert_not_called()
