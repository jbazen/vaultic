import os
import sqlite3
from contextlib import contextmanager

# Set test env vars BEFORE importing api modules
os.environ.setdefault("TESTING", "1")  # disables APScheduler in main.py (prevents 3-hour test hangs)
os.environ.setdefault("AUTH_USERNAME", "testuser")
os.environ.setdefault(
    "AUTH_PASSWORD_HASH",
    "$2b$12$85d09XUKy9n4srXnMzv8KObELsjfvWYLXtYziC1u7xkhiPqP0XyPm",  # "testpassword"
)
os.environ.setdefault("JWT_SECRET", "a" * 64)
os.environ.setdefault("JWT_EXPIRE_HOURS", "1")
os.environ.setdefault("ENCRYPTION_KEY", "c2VjcmV0c2VjcmV0c2VjcmV0c2VjcmV0c2VjcmV0c2U=")
os.environ.setdefault("PLAID_CLIENT_ID", "test_client_id")
os.environ.setdefault("PLAID_SECRET", "test_secret")
os.environ.setdefault("PLAID_ENV", "sandbox")

import api.database as db_module

_test_conn = None


@contextmanager
def _test_get_db():
    global _test_conn
    if _test_conn is None:
        _test_conn = sqlite3.connect(":memory:", check_same_thread=False)
        _test_conn.row_factory = sqlite3.Row
        _test_conn.execute("PRAGMA foreign_keys = ON")
        _test_conn.executescript(db_module.SCHEMA)
        _test_conn.commit()
    try:
        yield _test_conn
        _test_conn.commit()
    except Exception:
        _test_conn.rollback()
        raise


db_module.get_db = _test_get_db

import pytest
from fastapi.testclient import TestClient
from api.main import app
from api.auth import create_token


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def valid_token():
    return create_token("testuser")


@pytest.fixture(scope="session")
def auth_headers(valid_token):
    return {"Authorization": f"Bearer {valid_token}"}
