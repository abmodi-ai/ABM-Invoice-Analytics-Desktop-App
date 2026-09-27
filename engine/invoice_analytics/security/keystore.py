"""Protects the install KeySet at rest.

Windows: DPAPI, user scope (CryptProtectData), blob stored in the data dir.
macOS: the login Keychain via `keyring`.
Linux: the desktop's Secret Service keyring (GNOME Keyring, KWallet) via `keyring`. Minimal desktops
and servers often have none; there the key is kept in a file readable only by this OS user
(`file`), and a warning is logged. Once an install has a key file it keeps using it, so a keyring
that appears later never orphans the database.
`file` is also the mode used by tests and CI.
"""

from __future__ import annotations

import contextlib
import ctypes
import logging
import os
import sys
from pathlib import Path

from invoice_analytics.security.crypto import KeySet, unwrap_with_passphrase, wrap_with_passphrase

KEYRING_SERVICE = "abm-invoice-analytics"


class KeystoreError(RuntimeError):
    pass


# ---------------------------------------------------------------- DPAPI (Windows)
class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi(data: bytes, protect: bool) -> bytes:  # pragma: no cover - Windows only
    crypt32 = ctypes.windll.crypt32  # type: ignore[attr-defined,unused-ignore]
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined,unused-ignore]
    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = _DataBlob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = _DataBlob()
    entropy_raw = b"invoice-analytics-keyset-v1"
    ebuf = ctypes.create_string_buffer(entropy_raw, len(entropy_raw))
    entropy = _DataBlob(len(entropy_raw), ctypes.cast(ebuf, ctypes.POINTER(ctypes.c_char)))
    CRYPTPROTECT_UI_FORBIDDEN = 0x1
    if protect:
        ok = crypt32.CryptProtectData(
            ctypes.byref(blob_in),
            ctypes.c_wchar_p("invoice-analytics"),
            ctypes.byref(entropy),
            None,
            None,
            CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(blob_out),
        )
    else:
        ok = crypt32.CryptUnprotectData(
            ctypes.byref(blob_in),
            None,
            ctypes.byref(entropy),
            None,
            None,
            CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(blob_out),
        )
    if not ok:
        raise KeystoreError(f"DPAPI call failed: {ctypes.GetLastError()}")  # type: ignore[attr-defined,unused-ignore]
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def keyring_usable() -> bool:
    """True when an OS keyring backend is actually available (not keyring's 'fail' fallback)."""
    try:
        import keyring
        from keyring.backends import fail

        kr = keyring.get_keyring()
        if isinstance(kr, fail.Keyring) or getattr(kr, "backends", None) == []:
            return False
        keyring.get_password(KEYRING_SERVICE, "probe")
        return True
    except Exception:  # noqa: BLE001 - no D-Bus, locked or missing keyring: fall back
        return False


class Keystore:
    def __init__(self, data_dir: Path, mode: str = "auto") -> None:
        self.data_dir = data_dir
        if mode == "auto":
            if sys.platform == "win32":
                mode = "dpapi"
            elif sys.platform.startswith("linux") and ((data_dir / "keyset.bin").exists() or not keyring_usable()):
                mode = "file"
                logging.getLogger("invoice_analytics.keystore").warning(
                    "no OS keyring available: the database key is protected by file permissions only;"
                    " use full-disk encryption or install GNOME Keyring/KWallet"
                )
            else:
                mode = "keyring"
        self.mode = mode
        self._account = f"keyset:{data_dir.resolve()}"

    @property
    def blob_path(self) -> Path:
        return self.data_dir / "keyset.bin"

    @property
    def recovery_path(self) -> Path:
        return self.data_dir / "recovery.bin"

    def exists(self) -> bool:
        if self.mode in ("dpapi", "file"):
            return self.blob_path.exists()
        import keyring

        return keyring.get_password(KEYRING_SERVICE, self._account) is not None

    def load(self) -> KeySet:
        if self.mode == "dpapi":
            return KeySet.from_bytes(_dpapi(self.blob_path.read_bytes(), protect=False))
        if self.mode == "file":
            return KeySet.from_bytes(self.blob_path.read_bytes())
        import keyring

        raw = keyring.get_password(KEYRING_SERVICE, self._account)
        if raw is None:
            raise KeystoreError("no keyset in OS keyring")
        return KeySet.from_bytes(raw.encode())

    def save(self, keys: KeySet) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        raw = keys.to_bytes()
        if self.mode == "dpapi":
            self._write_private(self.blob_path, _dpapi(raw, protect=True))
        elif self.mode == "file":
            self._write_private(self.blob_path, raw)
        else:
            import keyring

            keyring.set_password(KEYRING_SERVICE, self._account, raw.decode())

    def delete(self) -> None:
        if self.mode == "keyring":
            import keyring

            with contextlib.suppress(Exception):  # already absent is fine
                keyring.delete_password(KEYRING_SERVICE, self._account)
        for p in (self.blob_path, self.recovery_path):
            if p.exists():
                p.unlink()

    def load_or_create(self) -> tuple[KeySet, bool]:
        if self.exists():
            return self.load(), False
        keys = KeySet.generate()
        self.save(keys)
        return keys, True

    # Recovery key: an optional admin passphrase-wrapped copy of the keyset.
    def write_recovery(self, keys: KeySet, recovery_key: str) -> None:
        self._write_private(self.recovery_path, wrap_with_passphrase(keys.to_bytes(), recovery_key))

    def recover(self, recovery_key: str) -> KeySet:
        return KeySet.from_bytes(unwrap_with_passphrase(self.recovery_path.read_bytes(), recovery_key))

    @staticmethod
    def _write_private(path: Path, data: bytes) -> None:
        tmp = path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
