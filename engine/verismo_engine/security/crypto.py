"""Key material, keyed hashing and field-level encryption.

Three independent 256-bit keys are generated at install:
  db_key    -> SQLCipher page encryption
  hmac_key  -> pseudonymous matching keys (patient_key, tax IDs, source IDs)
  field_key -> AES-256-GCM for *_enc columns (defense in depth on top of SQLCipher)
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

KEY_BYTES = 32
_FIELD_PREFIX = b"v1:"


@dataclass(frozen=True)
class KeySet:
    db_key: bytes
    hmac_key: bytes
    field_key: bytes

    @classmethod
    def generate(cls) -> KeySet:
        return cls(
            secrets.token_bytes(KEY_BYTES),
            secrets.token_bytes(KEY_BYTES),
            secrets.token_bytes(KEY_BYTES),
        )

    def to_bytes(self) -> bytes:
        return json.dumps(
            {
                "v": 1,
                "db": self.db_key.hex(),
                "hmac": self.hmac_key.hex(),
                "field": self.field_key.hex(),
            }
        ).encode()

    @classmethod
    def from_bytes(cls, raw: bytes) -> KeySet:
        d = json.loads(raw)
        return cls(bytes.fromhex(d["db"]), bytes.fromhex(d["hmac"]), bytes.fromhex(d["field"]))


def keyed_hash(key: bytes, value: str, domain: str) -> str:
    """HMAC-SHA256 with a domain separator so equal values in different domains differ."""
    mac = hmac.new(key, f"{domain}\x1f{value}".encode(), hashlib.sha256)
    return mac.hexdigest()


class FieldCipher:
    """AES-256-GCM for individual column values. Output is ASCII-safe for TEXT columns."""

    def __init__(self, key: bytes) -> None:
        self._aead = AESGCM(key)

    def encrypt(self, plaintext: str | None, aad: str = "") -> str | None:
        if plaintext is None:
            return None
        nonce = secrets.token_bytes(12)
        ct = self._aead.encrypt(nonce, plaintext.encode(), aad.encode())
        return (_FIELD_PREFIX + base64.urlsafe_b64encode(nonce + ct)).decode()

    def decrypt(self, token: str | None, aad: str = "") -> str | None:
        if token is None:
            return None
        raw = token.encode()
        if not raw.startswith(_FIELD_PREFIX):
            raise ValueError("unknown field cipher version")
        blob = base64.urlsafe_b64decode(raw[len(_FIELD_PREFIX) :])
        return self._aead.decrypt(blob[:12], blob[12:], aad.encode()).decode()


def wrap_with_passphrase(secret: bytes, passphrase: str) -> bytes:
    """Wrap key material with a passphrase (recovery key). scrypt + AES-GCM."""
    salt = secrets.token_bytes(16)
    kek = Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(passphrase.encode())
    nonce = secrets.token_bytes(12)
    ct = AESGCM(kek).encrypt(nonce, secret, b"verismo-recovery")
    return json.dumps({"v": 1, "salt": salt.hex(), "nonce": nonce.hex(), "ct": ct.hex()}).encode()


def unwrap_with_passphrase(blob: bytes, passphrase: str) -> bytes:
    d: dict[str, Any] = json.loads(blob)
    kek = Scrypt(salt=bytes.fromhex(d["salt"]), length=32, n=2**15, r=8, p=1).derive(passphrase.encode())
    return AESGCM(kek).decrypt(bytes.fromhex(d["nonce"]), bytes.fromhex(d["ct"]), b"verismo-recovery")


def new_recovery_key() -> str:
    """Human-transcribable recovery key: 8 groups of 5 base32 chars (200 bits)."""
    raw = base64.b32encode(secrets.token_bytes(25)).decode().rstrip("=")
    return "-".join(raw[i : i + 5] for i in range(0, 40, 5))


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def sha256_hex(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode()
    return hashlib.sha256(data).hexdigest()


def random_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)
