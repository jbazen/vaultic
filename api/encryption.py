"""Fernet symmetric encryption for Plaid access tokens stored at rest."""
import os
from cryptography.fernet import Fernet


def _get_cipher() -> Fernet:
    key = os.environ.get("ENCRYPTION_KEY", "")
    if not key:
        raise RuntimeError("ENCRYPTION_KEY is not set — cannot encrypt/decrypt Plaid tokens")
    return Fernet(key.encode())


def encrypt(plaintext: str) -> str:
    return _get_cipher().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    return _get_cipher().decrypt(ciphertext.encode()).decode()
