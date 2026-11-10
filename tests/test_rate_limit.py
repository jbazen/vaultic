"""Tests for rate limiting on the sync endpoint."""
import time
import pytest
from api import rate_limit


class TestSyncRateLimit:
    def setup_method(self):
        rate_limit._sync_calls.clear()

    def test_under_limit_is_allowed(self):
        ok, remaining = rate_limit.check_sync("user1")
        assert ok is False
        assert remaining > 0

    def test_at_limit_is_blocked(self):
        now = time.time()
        rate_limit._sync_calls["user1"] = [now] * rate_limit.SYNC_MAX
        limited, remaining = rate_limit.check_sync("user1")
        assert limited is True
        assert remaining == 0

    def test_old_sync_calls_expire(self):
        old_time = time.time() - rate_limit.SYNC_WINDOW - 1
        rate_limit._sync_calls["user1"] = [old_time] * rate_limit.SYNC_MAX
        limited, _ = rate_limit.check_sync("user1")
        assert limited is False

    def test_sync_record_increments(self):
        rate_limit.record_sync("user1")
        _, remaining = rate_limit.check_sync("user1")
        assert remaining == rate_limit.SYNC_MAX - 1

    def test_different_users_isolated(self):
        now = time.time()
        rate_limit._sync_calls["userA"] = [now] * rate_limit.SYNC_MAX
        limited_a, _ = rate_limit.check_sync("userA")
        limited_b, _ = rate_limit.check_sync("userB")
        assert limited_a is True
        assert limited_b is False
