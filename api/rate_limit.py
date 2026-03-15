"""
Simple in-memory rate limiters for specific endpoints.
Separate from login rate limiting — these protect API cost and abuse.
"""
import time
from collections import defaultdict

# {user: [timestamps]}
_sage_calls: dict[str, list[float]] = defaultdict(list)
_sync_calls: dict[str, list[float]] = defaultdict(list)

SAGE_MAX = 60       # messages per hour per user
SAGE_WINDOW = 3600

SYNC_MAX = 5        # syncs per 5 minutes per user
SYNC_WINDOW = 300


def _check(store: dict, key: str, max_calls: int, window: int) -> tuple[bool, int]:
    """Returns (is_limited, remaining)."""
    now = time.time()
    store[key] = [t for t in store[key] if now - t < window]
    remaining = max(0, max_calls - len(store[key]))
    return len(store[key]) >= max_calls, remaining


def check_sage(username: str) -> tuple[bool, int]:
    return _check(_sage_calls, username, SAGE_MAX, SAGE_WINDOW)


def record_sage(username: str):
    _sage_calls[username].append(time.time())


def check_sync(username: str) -> tuple[bool, int]:
    return _check(_sync_calls, username, SYNC_MAX, SYNC_WINDOW)


def record_sync(username: str):
    _sync_calls[username].append(time.time())
