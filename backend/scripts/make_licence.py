#!/usr/bin/env python3
"""Issue Packrat licence keys (vendor side - never ships to customers).

  python scripts/make_licence.py --init
      Generates a new Ed25519 keypair: writes licensing/private.pem and
      prints the public key to paste into app/core/licence.py. Do this once.

  python scripts/make_licence.py --tier colony --customer "Acme Ltd" --expires 2027-09-16
      Prints a key. --devices overrides the tier's device cap, --expires is
      optional (no expiry for a perpetual key), --private-key points at the
      pem (default licensing/private.pem or $PACKRAT_LICENCE_PRIVATE_KEY).

  python scripts/make_licence.py --show PKR1....
      Decodes a key without needing the private key.

Keep licensing/private.pem out of the repository (it is gitignored) and
back it up: anyone holding it can mint licences, and losing it means every
future key needs a new public key shipped in a release.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_KEY = ROOT / "licensing" / "private.pem"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def init(path: Path) -> None:
    if path.exists():
        sys.exit(f"{path} already exists - refusing to overwrite a signing key")
    path.parent.mkdir(parents=True, exist_ok=True)
    key = Ed25519PrivateKey.generate()
    path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    pub = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    print(f"Wrote {path}")
    print("Public key (paste into PUBLIC_KEY_B64 in backend/app/core/licence.py):")
    print(base64.b64encode(pub).decode())


def sign(path: Path, tier: str, customer: str, expires: str | None, devices: int | None) -> str:
    if not path.exists():
        sys.exit(f"No private key at {path}. Run with --init first, or pass --private-key.")
    key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        sys.exit("The private key is not an Ed25519 key")
    if expires:
        date.fromisoformat(expires)  # validate
    payload = {
        "v": 1,
        "id": secrets.token_hex(4),
        "tier": tier,
        "customer": customer,
        "issued": datetime.now(timezone.utc).date().isoformat(),
        "expires": expires,
    }
    if devices is not None:
        payload["devices"] = devices
    payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    signature = key.sign(payload_bytes)
    return f"PKR1.{_b64url(payload_bytes)}.{_b64url(signature)}"


def show(key: str) -> None:
    parts = key.strip().split(".")
    if len(parts) != 3:
        sys.exit("Not a PKR1 key")
    payload = base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4))
    print(json.dumps(json.loads(payload), indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--init", action="store_true", help="generate a new signing keypair")
    parser.add_argument("--tier", choices=["colony", "warren"])
    parser.add_argument("--customer", default="")
    parser.add_argument("--expires", help="YYYY-MM-DD; omit for a perpetual key")
    parser.add_argument("--devices", type=int, help="override the tier's device cap")
    parser.add_argument("--private-key", default=os.environ.get("PACKRAT_LICENCE_PRIVATE_KEY", str(DEFAULT_KEY)))
    parser.add_argument("--show", metavar="KEY", help="decode a key's payload")
    args = parser.parse_args()

    path = Path(args.private_key)
    if args.init:
        init(path)
    elif args.show:
        show(args.show)
    elif args.tier:
        print(sign(path, args.tier, args.customer, args.expires, args.devices))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
