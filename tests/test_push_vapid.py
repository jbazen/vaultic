"""Tests for the VAPID JWT contact claim in api/push.py (#93)."""

import logging

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import (
    Encoding, NoEncryption, PrivateFormat,
)

from api import push

ENDPOINT = "https://fcm.googleapis.com/fcm/send/abc"


@pytest.fixture
def vapid_key(monkeypatch):
    """Configure a throwaway VAPID private key and return its public key."""
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()).decode()
    monkeypatch.setenv("VAPID_PRIVATE_KEY_PEM", pem)
    return key.public_key()


def _claims(public_key) -> dict:
    token = push._create_vapid_jwt(ENDPOINT)
    return jwt.decode(token, public_key, algorithms=["ES256"], audience="https://fcm.googleapis.com")


def test_uses_vapid_email_when_set(vapid_key, monkeypatch, caplog):
    monkeypatch.setenv("VAPID_EMAIL", "mailto:admin@example.com")
    with caplog.at_level(logging.WARNING, logger=push.logger.name):
        claims = _claims(vapid_key)
    assert claims["sub"] == "mailto:admin@example.com"
    assert claims["aud"] == "https://fcm.googleapis.com"
    assert "VAPID_EMAIL" not in caplog.text


@pytest.mark.parametrize("value", [None, "", "   "])
def test_falls_back_to_site_url_and_warns(vapid_key, monkeypatch, caplog, value):
    if value is None:
        monkeypatch.delenv("VAPID_EMAIL", raising=False)
    else:
        monkeypatch.setenv("VAPID_EMAIL", value)
    with caplog.at_level(logging.WARNING, logger=push.logger.name):
        claims = _claims(vapid_key)
    assert claims["sub"] == push.VAPID_FALLBACK_SUB == "https://vaulticsage.com"
    assert "VAPID_EMAIL is not set" in caplog.text
