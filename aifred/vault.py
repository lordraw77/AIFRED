"""Secrets at rest (spec section 4.2).

Two interchangeable backends encrypt the values kept in the ``secrets`` table:

* :class:`SecretBox`: AES-GCM under a random master key in ``data/.secret.key`` (mode 0600).
  Simple, but the key sits on the same volume as the database.
* :class:`OpenBaoBox`: OpenBao (or Vault) **Transit**. The master key never leaves the
  OpenBao server; AIFRED sends it values to encrypt and decrypt.

In both, the secret's name is bound to the ciphertext, so a value copied to another row
fails to decrypt. Stored blobs are self-describing (``\\x01…`` local, ``vault:v1:…``
Transit), which lets a migration read one format and write the other.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

import httpx
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from aifred.store import Store

logger = logging.getLogger(__name__)

KEY_BYTES = 32
NONCE_BYTES = 12
_FORMAT = b"\x01"
_TRANSIT_PREFIX = b"vault:"


class VaultError(RuntimeError):
    """A secret cannot be decrypted or encrypted for good (wrong key, tampered or malformed data)."""


class VaultUnavailable(VaultError):
    """The secret store cannot be reached right now (OpenBao sealed, down or refusing access).

    Callers must treat this as a temporary outage and must not conclude that a secret is
    missing or invalid.
    """


def load_or_create_key(path: Path) -> bytes:
    """Read the master key, creating it with mode 0600 on first start.

    Raises:
        VaultError: If an existing key file has the wrong size.
    """
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return read_key(path)
    key = AESGCM.generate_key(bit_length=KEY_BYTES * 8)
    with os.fdopen(fd, "wb") as fh:
        fh.write(key)
    logger.info("Generated a new master key in %s; back it up together with the database", path)
    return key


def read_key(path: Path) -> bytes:
    """Read an existing master key file.

    Raises:
        VaultError: If the file has the wrong size.
    """
    key = path.read_bytes()
    if len(key) != KEY_BYTES:
        raise VaultError(f"{path} is not a valid master key ({len(key)} bytes)")
    if path.stat().st_mode & 0o077:
        logger.warning("%s is readable by group or others; run chmod 600 on it", path)
    return key


class Box(Protocol):
    """An encryption backend."""

    label: str

    def handles(self, blob: bytes) -> bool:
        """Whether a stored blob is in this backend's format."""

    def encrypt(self, plaintext: str, name: str) -> bytes:
        """Encrypt a value bound to its name."""

    def decrypt(self, blob: bytes, name: str) -> str:
        """Decrypt a value."""


class SecretBox:
    """AES-GCM with a per-value random nonce and a master key held locally.

    Args:
        key: 32-byte master key.
    """

    label = "file"

    def __init__(self, key: bytes) -> None:
        self._aead = AESGCM(key)

    def handles(self, blob: bytes) -> bool:
        return blob[:1] == _FORMAT

    def encrypt(self, plaintext: str, name: str) -> bytes:
        nonce = os.urandom(NONCE_BYTES)
        return _FORMAT + nonce + self._aead.encrypt(nonce, plaintext.encode(), name.encode())

    def decrypt(self, blob: bytes, name: str) -> str:
        if len(blob) < 1 + NONCE_BYTES or blob[:1] != _FORMAT:
            raise VaultError(f"secret {name!r} has an unknown format")
        nonce, ciphertext = blob[1 : 1 + NONCE_BYTES], blob[1 + NONCE_BYTES :]
        try:
            return self._aead.decrypt(nonce, ciphertext, name.encode()).decode()
        except InvalidTag:
            raise VaultError(f"secret {name!r} cannot be decrypted (wrong master key?)") from None


class OpenBaoBox:
    """Encryption through the Transit secrets engine of OpenBao (or HashiCorp Vault).

    Authentication is either a static token or AppRole; with AppRole the client logs in
    again by itself when the token expires. The secret name travels inside the encrypted
    envelope, so a ciphertext copied to another row is rejected.

    Args:
        addr: Server URL, for example ``http://openbao:8200``.
        token: Static token (mutually exclusive with AppRole).
        role_id: AppRole role id.
        secret_id: AppRole secret id.
        mount: Transit mount path.
        key: Transit key name.
        verify: TLS verification: ``True``, ``False`` or a CA bundle path.
        timeout: Request timeout in seconds.
        client_factory: Builds the HTTP client (replaceable in tests).
    """

    label = "openbao"

    def __init__(
        self,
        addr: str,
        *,
        token: str | None = None,
        role_id: str | None = None,
        secret_id: str | None = None,
        mount: str = "transit",
        key: str = "aifred",
        verify: bool | str = True,
        timeout: float = 10.0,
        client_factory: Callable[[], httpx.Client] | None = None,
    ) -> None:
        if not token and not (role_id and secret_id):
            raise VaultError("OpenBao needs a token, or a role id and a secret id")
        self._addr = addr.rstrip("/")
        self._static_token = token
        self._token = token
        self._role_id, self._secret_id = role_id, secret_id
        self._mount, self._key = mount.strip("/"), key
        self._lock = threading.Lock()
        self._client = (client_factory or (lambda: httpx.Client(verify=verify, timeout=httpx.Timeout(timeout, connect=3.0))))()

    def handles(self, blob: bytes) -> bool:
        return blob.startswith(_TRANSIT_PREFIX)

    # HTTP

    def _login(self) -> None:
        try:
            r = self._client.post(
                f"{self._addr}/v1/auth/approle/login", json={"role_id": self._role_id, "secret_id": self._secret_id}
            )
        except httpx.HTTPError as exc:
            raise VaultUnavailable(f"OpenBao is unreachable ({type(exc).__name__})") from None
        if r.status_code == 503:
            raise VaultUnavailable("OpenBao is sealed")
        if r.status_code >= 400:
            raise VaultUnavailable(f"OpenBao rejected the AppRole login (HTTP {r.status_code}); check the role id and secret id")
        self._token = r.json()["auth"]["client_token"]
        logger.info("Logged in to OpenBao with AppRole")

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            relogin = bool(self._role_id)
            if self._token is None:
                self._login()
                relogin = False
            for attempt in (0, 1):
                try:
                    r = self._client.post(f"{self._addr}/v1/{path}", json=body, headers={"X-Vault-Token": self._token or ""})
                except httpx.HTTPError as exc:
                    raise VaultUnavailable(f"OpenBao is unreachable ({type(exc).__name__})") from None
                if r.status_code == 403 and attempt == 0 and relogin and not self._static_token:
                    self._token = None  # the token expired or was revoked: log in once more
                    self._login()
                    continue
                break
        if r.status_code == 503:
            raise VaultUnavailable("OpenBao is sealed")
        if r.status_code == 403:
            raise VaultUnavailable("OpenBao denied access: check the token, the policy and the Transit key name")
        if r.status_code == 404:
            raise VaultUnavailable(f"OpenBao has no Transit mount {self._mount!r}")
        if r.status_code == 400:
            detail = "; ".join(r.json().get("errors", [])) if r.headers.get("content-type", "").startswith("application/json") else ""
            raise VaultError(f"OpenBao could not process the value ({detail or 'bad request'})")
        if r.status_code >= 400:
            raise VaultUnavailable(f"OpenBao answered HTTP {r.status_code}")
        data: dict[str, Any] = r.json()["data"]
        return data

    # Box

    def encrypt(self, plaintext: str, name: str) -> bytes:
        envelope = json.dumps({"n": name, "v": plaintext}).encode()
        data = self._post(f"{self._mount}/encrypt/{self._key}", {"plaintext": base64.b64encode(envelope).decode()})
        return str(data["ciphertext"]).encode()

    def decrypt(self, blob: bytes, name: str) -> str:
        data = self._post(f"{self._mount}/decrypt/{self._key}", {"ciphertext": blob.decode()})
        try:
            envelope = json.loads(base64.b64decode(data["plaintext"]))
            if envelope["n"] != name:
                raise KeyError
            return str(envelope["v"])
        except (KeyError, ValueError, TypeError):
            raise VaultError(f"secret {name!r} is bound to a different name") from None

    def health(self) -> tuple[bool, str]:
        """Check the server without needing a valid token.

        Returns:
            ``(usable, detail)``.
        """
        try:
            r = self._client.get(f"{self._addr}/v1/sys/health", params={"standbyok": "true"})
        except httpx.HTTPError as exc:
            return False, f"unreachable ({type(exc).__name__})"
        return {
            200: (True, "ready"), 429: (True, "ready (standby)"), 501: (False, "not initialised"),
            503: (False, "sealed"),
        }.get(r.status_code, (False, f"HTTP {r.status_code}"))

    def close(self) -> None:
        """Close the HTTP client."""
        self._client.close()


class Vault:
    """Named secrets stored encrypted in the ``secrets`` table.

    Args:
        store: Database store.
        primary: Backend used to encrypt new values.
        others: Further backends that can still read their own blobs (migration, legacy files).
    """

    def __init__(self, store: Store, primary: Box, others: list[Box] | None = None) -> None:
        self._store = store
        self._primary = primary
        self._boxes: list[Box] = [primary, *(others or [])]
        self._cache: dict[str, tuple[bytes, str]] = {}
        self._cache_lock = threading.Lock()

    @property
    def backend(self) -> str:
        """Label of the backend that encrypts new values."""
        return self._primary.label

    def _box_for(self, blob: bytes, name: str) -> Box:
        for box in self._boxes:
            if box.handles(blob):
                return box
        kind = "OpenBao" if blob.startswith(_TRANSIT_PREFIX) else "a local master key"
        raise VaultError(f"secret {name!r} needs {kind}, which is not configured")

    def set(self, name: str, value: str) -> None:
        """Store or replace a secret.

        Raises:
            VaultUnavailable: If the secret store cannot be reached.
        """
        blob = self._primary.encrypt(value, name)
        self._store.secret_set(name, blob)
        with self._cache_lock:
            self._cache[name] = (blob, value)

    def get(self, name: str) -> str | None:
        """Return a decrypted secret, or ``None`` if unset or undecryptable.

        Raises:
            VaultUnavailable: If the backend cannot be reached; the secret may well exist.
        """
        blob = self._store.secret_get(name)
        if blob is None:
            return None
        with self._cache_lock:
            cached = self._cache.get(name)
        if cached and cached[0] == blob:
            return cached[1]
        try:
            value = self._box_for(blob, name).decrypt(blob, name)
        except VaultUnavailable:
            raise
        except VaultError as exc:
            logger.error("%s; re-enter it from the dashboard", exc)
            return None
        with self._cache_lock:
            self._cache[name] = (blob, value)
        return value

    def exists(self, name: str) -> bool:
        """Whether a secret is stored (does not contact the backend)."""
        return self._store.secret_get(name) is not None

    def delete(self, name: str) -> bool:
        """Remove a secret; returns whether it existed."""
        with self._cache_lock:
            self._cache.pop(name, None)
        return self._store.secret_delete(name)

    def last4(self, name: str) -> str | None:
        """Last four characters of a secret, for display next to provider keys."""
        value = self.get(name)
        return value[-4:] if value and len(value) >= 8 else None

    def migrate(self) -> dict[str, int]:
        """Re-encrypt every secret with the primary backend.

        Returns:
            Counts: ``migrated``, ``unchanged`` and ``failed``.

        Raises:
            VaultUnavailable: If a backend cannot be reached (nothing is half-written: each value is replaced atomically).
        """
        counts = {"migrated": 0, "unchanged": 0, "failed": 0}
        for name in self._store.secret_names():
            blob = self._store.secret_get(name)
            if blob is None:
                continue
            if self._primary.handles(blob):
                counts["unchanged"] += 1
                continue
            try:
                value = self._box_for(blob, name).decrypt(blob, name)
            except VaultUnavailable:
                raise
            except VaultError as exc:
                logger.error("Cannot migrate %s: %s", name, exc)
                counts["failed"] += 1
                continue
            self.set(name, value)
            counts["migrated"] += 1
        return counts

    def legacy_count(self) -> int:
        """Number of secrets not in the primary backend's format."""
        return sum(1 for n in self._store.secret_names() if not self._primary.handles(self._store.secret_get(n) or b""))

    def status(self) -> dict[str, Any]:
        """Backend, reachability and leftovers, for the System page and the startup log."""
        info: dict[str, Any] = {"backend": self.backend, "ok": True, "detail": "ready", "legacy_secrets": self.legacy_count()}
        if isinstance(self._primary, OpenBaoBox):
            info["ok"], info["detail"] = self._primary.health()
        return info
