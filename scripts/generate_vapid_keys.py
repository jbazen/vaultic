"""Generate VAPID key pair for Web Push notifications.

Run once on the server, then add the output to your .env file.
The private key is secret — never commit it to source control.

Usage:
    python scripts/generate_vapid_keys.py
"""

import base64
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import (
    Encoding, PublicFormat, PrivateFormat, NoEncryption,
)


def generate():
    # Generate a P-256 (secp256r1) key pair — required by the Web Push spec
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_key  = private_key.public_key()

    # Public key: uncompressed point (65 bytes: 0x04 || X || Y), base64url-encoded
    pub_bytes  = public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    vapid_pub  = base64.urlsafe_b64encode(pub_bytes).rstrip(b"=").decode()

    # Private key: PEM format (the \n chars must be preserved — use single quotes in .env)
    vapid_priv = private_key.private_bytes(
        Encoding.PEM, PrivateFormat.TraditionalOpenSSL, NoEncryption()
    ).decode()

    print("=" * 60)
    print("Add the following lines to your server .env file:")
    print("=" * 60)
    print()
    print(f"VAPID_PUBLIC_KEY={vapid_pub}")
    print()
    # Collapse the PEM to a single line with \\n for .env storage
    pem_single_line = vapid_priv.replace("\n", "\\n").strip()
    print(f"VAPID_PRIVATE_KEY_PEM={pem_single_line}")
    print()
    print("VAPID_EMAIL=mailto:your-email@example.com")
    print()
    print("=" * 60)
    print("IMPORTANT: Keep VAPID_PRIVATE_KEY_PEM secret. Never commit it.")
    print("=" * 60)


if __name__ == "__main__":
    generate()
