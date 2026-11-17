"""Legacy standalone signing utility; never required by the free MIT auditor.

Retained for historical reference. Keep any private signing keys confidential.
"""
from __future__ import annotations

import argparse
import base64
import os
import re
from datetime import date
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from license_manager import canonical, encode


def generate_license(private_key: Ed25519PrivateKey, firm: str, expires: str) -> str:
    if not 1 <= len(firm.strip()) <= 200:
        raise ValueError("Firm name must contain 1 to 200 characters.")
    expiry = date.fromisoformat(expires)
    if expiry.isoformat() != expires:
        raise ValueError("Expiration must use YYYY-MM-DD.")
    raw = canonical({"version": 1, "firm": firm.strip(), "expires": expires})
    return f"VP1.{encode(raw)}.{encode(private_key.sign(raw))}"


def main() -> None:
    parser = argparse.ArgumentParser(description="VeriPact offline license signing; founder workstation only")
    commands = parser.add_subparsers(dest="command", required=True)
    keygen = commands.add_parser("keygen", help="Create the founder signing key once")
    keygen.add_argument("--private-key", required=True, type=Path)
    keygen.add_argument("--install-public-key", action="store_true", help="Embed the new public key into license_manager.py before distribution")
    issue = commands.add_parser("issue", help="Sign a license for a firm")
    issue.add_argument("--private-key", required=True, type=Path)
    issue.add_argument("--firm", required=True)
    issue.add_argument("--expires", required=True, help="YYYY-MM-DD, inclusive UTC")
    args = parser.parse_args()
    try:
        if args.command == "keygen":
            key = Ed25519PrivateKey.generate()
            pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
            # Exclusive create prevents accidentally replacing a production signing key.
            fd = os.open(args.private_key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(pem)
            public = base64.b64encode(key.public_key().public_bytes_raw()).decode("ascii")
            if args.install_public_key:
                target = Path(__file__).with_name("license_manager.py")
                content = target.read_text(encoding="utf-8")
                updated, count = re.subn(r'^PUBLIC_KEY_B64 = "[^"]+"$', f'PUBLIC_KEY_B64 = "{public}"', content, flags=re.MULTILINE)
                if count != 1:
                    raise ValueError("Cannot find unique PUBLIC_KEY_B64; install the public key manually.")
                target.write_text(updated, encoding="utf-8")
            print(f"Public verification key: {public}")
            print("Keep the private PEM offline. Never include it in a client build.")
        else:
            key = serialization.load_pem_private_key(args.private_key.read_bytes(), password=None)
            if not isinstance(key, Ed25519PrivateKey):
                raise ValueError("An Ed25519 private key is required.")
            print(generate_license(key, args.firm, args.expires))
    except (OSError, ValueError) as exc:
        parser.exit(2, f"License operation failed: {exc}\n")


if __name__ == "__main__":
    main()
