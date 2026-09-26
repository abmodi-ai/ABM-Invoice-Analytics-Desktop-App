"""Generate an Ed25519 signing key pair for reference-data bundles.

    uv run python refdata/make_signing_key.py refdata/keys/release-signing

Writes <name>.private (hex; keep OFFLINE, never commit) and prints the public key, which must be
added to TRUSTED_PUBLIC_KEYS in engine/verismo_engine/clinical/refdata.py for release builds.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization as s
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def main() -> int:
    base = Path(sys.argv[1] if len(sys.argv) > 1 else "refdata/keys/release-signing")
    k = Ed25519PrivateKey.generate()
    priv = base.with_suffix(".private")
    fd = os.open(priv, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(k.private_bytes(s.Encoding.Raw, s.PrivateFormat.Raw, s.NoEncryption()).hex())
    pub = k.public_key().public_bytes(s.Encoding.Raw, s.PublicFormat.Raw).hex()
    base.with_suffix(".public").write_text(pub + "\n")
    print(f"private key: {priv} (keep offline)\npublic key:  {pub}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
