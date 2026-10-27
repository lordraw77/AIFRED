"""Admin account, login sessions, rate limiting and API keys (spec section 4.1)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Literal

from aifred.store import Store

logger = logging.getLogger(__name__)

SESSION_TTL = 12 * 3600
"""Sliding lifetime of a login session (s)."""

MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 1024

_SCRYPT_N, _SCRYPT_R, _SCRYPT_P, _SCRYPT_DKLEN = 2**15, 8, 1, 64
_SCRYPT_MAXMEM = 64 * 1024 * 1024
_TOUCH_INTERVAL = 60.0
API_KEY_PREFIX = "aif_"

Scope = Literal["chat", "admin"]


class AuthError(Exception):
    """Authentication failed."""


class RateLimited(AuthError):
    """Too many login attempts.

    Attributes:
        retry_after: Seconds until the next attempt is allowed.
    """

    def __init__(self, retry_after: int) -> None:
        super().__init__(f"too many attempts, retry in {retry_after} s")
        self.retry_after = retry_after


def validate_password(password: str) -> None:
    """Check password length rules.

    Raises:
        ValueError: If the password is too short or too long.
    """
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"the password must be at least {MIN_PASSWORD_LENGTH} characters")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise ValueError(f"the password must be at most {MAX_PASSWORD_LENGTH} characters")


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def hash_password(password: str) -> str:
    """Hash a password with scrypt (N=2^15, r=8, p=1, 16-byte salt).

    Returns:
        ``scrypt$N$r$p$<salt>$<hash>`` with Base64 fields.
    """
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P,
        maxmem=_SCRYPT_MAXMEM, dklen=_SCRYPT_DKLEN,
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    """Check a password against a stored hash in constant time."""
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(digest)
        actual = hashlib.scrypt(
            password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
            maxmem=_SCRYPT_MAXMEM, dklen=len(expected),
        )
    except (ValueError, TypeError):
        logger.error("Stored password hash is malformed")
        return False
    return hmac.compare_digest(actual, expected)


def _sha256(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass
class _Bucket:
    failures: deque[float] = field(default_factory=deque)
    blocked_until: float = 0.0
    level: int = 0
    last: float = 0.0


class LoginRateLimiter:
    """At most ``limit`` failed attempts per ``window`` per key, with exponential lockout.

    Keys are typically ``ip:<addr>`` and ``user:<name>``. Each lockout doubles the
    previous one, from ``base_lockout`` up to ``max_lockout``; a successful login
    resets the keys involved.
    """

    def __init__(
        self, limit: int = 5, window: float = 60.0, base_lockout: float = 60.0, max_lockout: float = 900.0
    ) -> None:
        self.limit = limit
        self.window = window
        self.base_lockout = base_lockout
        self.max_lockout = max_lockout
        self._buckets: dict[str, _Bucket] = {}
        self._lock = threading.Lock()

    def check(self, keys: list[str], now: float | None = None) -> int:
        """Return the seconds to wait before another attempt (0 if allowed)."""
        now = time.time() if now is None else now
        with self._lock:
            wait = max((b.blocked_until - now for k in keys if (b := self._buckets.get(k))), default=0.0)
        return max(0, int(wait + 0.999))

    def failure(self, keys: list[str], now: float | None = None) -> None:
        """Record a failed attempt."""
        now = time.time() if now is None else now
        with self._lock:
            self._prune(now)
            for key in keys:
                bucket = self._buckets.setdefault(key, _Bucket())
                bucket.last = now
                bucket.failures.append(now)
                while bucket.failures and bucket.failures[0] <= now - self.window:
                    bucket.failures.popleft()
                if len(bucket.failures) >= self.limit:
                    lockout = min(self.base_lockout * 2**bucket.level, self.max_lockout)
                    bucket.blocked_until = now + lockout
                    bucket.level += 1
                    bucket.failures.clear()

    def success(self, keys: list[str]) -> None:
        """Reset the keys after a successful login."""
        with self._lock:
            for key in keys:
                self._buckets.pop(key, None)

    def _prune(self, now: float) -> None:
        if len(self._buckets) < 10_000:
            return
        stale = [k for k, b in self._buckets.items() if b.last < now - 3600 and b.blocked_until < now]
        for key in stale:
            del self._buckets[key]


@dataclass(frozen=True)
class LoginSession:
    """An authenticated dashboard session.

    Attributes:
        token_hash: SHA-256 of the cookie token (the server-side id).
        csrf_token: Token expected in ``X-CSRF-Token``.
        expires: Expiry as epoch seconds.
    """

    token_hash: str
    csrf_token: str
    expires: float


@dataclass(frozen=True)
class ApiKey:
    """An authenticated API key.

    Attributes:
        id: Key id.
        name: Display name.
        scope: ``chat`` or ``admin``.
    """

    id: int
    name: str
    scope: Scope


class AuthService:
    """Admin account, login sessions and API keys.

    Args:
        store: Database store.
        limiter: Login rate limiter.
    """

    def __init__(self, store: Store, limiter: LoginRateLimiter | None = None) -> None:
        self._store = store
        self.limiter = limiter or LoginRateLimiter()
        # used to spend the same time on unknown usernames
        self._dummy_hash = hash_password(secrets.token_hex(16))

    def bootstrap_admin(self, username: str | None, password: str | None) -> None:
        """Create the admin from the bootstrap environment if none exists.

        Raises:
            ValueError: If no admin exists and the variables are missing or invalid.
        """
        if self._store.admin_get():
            if password:
                logger.warning(
                    "An admin account exists: AIFRED_ADMIN_PASSWORD is ignored, remove it from .env"
                )
            return
        if not username or not password:
            raise ValueError(
                "No admin account exists: set AIFRED_ADMIN_USER and AIFRED_ADMIN_PASSWORD for the first start"
            )
        validate_password(password)
        if self._store.admin_create(username.strip(), hash_password(password)):
            self._store.journal_add("settings", "admin", f"admin account {username.strip()!r} created")
            logger.info("Admin account created; remove AIFRED_ADMIN_PASSWORD from .env")

    @property
    def username(self) -> str | None:
        """The admin username, if an admin exists."""
        admin = self._store.admin_get()
        return admin["username"] if admin else None

    def login(
        self, username: str, password: str, ip: str | None, user_agent: str | None
    ) -> tuple[str, LoginSession]:
        """Check credentials and open a session.

        Returns:
            The cookie token (shown once) and the session.

        Raises:
            RateLimited: If the IP or username is locked out.
            AuthError: If the credentials are wrong.
        """
        keys = [f"ip:{ip or '-'}", f"user:{username.strip().lower()}"]
        wait = self.limiter.check(keys)
        if wait:
            raise RateLimited(wait)
        admin = self._store.admin_get()
        name_ok = bool(admin) and hmac.compare_digest(
            username.strip().encode(), (admin["username"] if admin else "").encode()
        )
        password_ok = verify_password(
            password[:MAX_PASSWORD_LENGTH], admin["password_hash"] if admin and name_ok else self._dummy_hash
        )
        if not (name_ok and password_ok):
            self.limiter.failure(keys)
            logger.warning("Failed login attempt")
            raise AuthError("invalid username or password")
        self.limiter.success(keys)
        token = secrets.token_urlsafe(32)
        session = LoginSession(_sha256(token), secrets.token_urlsafe(32), time.time() + SESSION_TTL)
        self._store.auth_session_purge(time.time())
        self._store.auth_session_create(
            session.token_hash, session.csrf_token, session.expires, ip, (user_agent or "")[:256] or None
        )
        logger.info("Admin logged in")
        return token, session

    def session(self, token: str) -> LoginSession | None:
        """Validate a cookie token and slide its expiry."""
        if not token:
            return None
        token_hash = _sha256(token)
        row = self._store.auth_session_get(token_hash)
        now = time.time()
        if not row or row["expires"] <= now:
            return None
        expires = row["expires"]
        if now - row["last_seen"] >= _TOUCH_INTERVAL:
            expires = now + SESSION_TTL
            self._store.auth_session_touch(token_hash, now, expires)
        return LoginSession(token_hash, row["csrf_token"], expires)

    def logout(self, token_hash: str) -> None:
        """Invalidate a session."""
        self._store.auth_session_delete(token_hash)

    def sessions(self) -> list[dict[str, object]]:
        """Active login sessions, identified by a short prefix of their hash."""
        return [
            {
                "id": row["token_hash"][:16],
                "created": row["created"],
                "last_seen": row["last_seen"],
                "expires": row["expires"],
                "ip": row["ip"],
                "user_agent": row["user_agent"],
            }
            for row in self._store.auth_session_list(time.time())
        ]

    def revoke_session(self, session_id: str) -> bool:
        """Invalidate the session whose hash starts with ``session_id``."""
        if len(session_id) != 16:
            return False
        for row in self._store.auth_session_list(time.time()):
            if hmac.compare_digest(row["token_hash"][:16], session_id):
                return self._store.auth_session_delete(row["token_hash"])
        return False

    def change_password(self, current: str, new: str, keep_session: str | None = None) -> None:
        """Change the admin password and invalidate the other sessions.

        Raises:
            AuthError: If the current password is wrong.
            ValueError: If the new password breaks the length rules.
        """
        admin = self._store.admin_get()
        if not admin or not verify_password(current[:MAX_PASSWORD_LENGTH], admin["password_hash"]):
            raise AuthError("the current password is wrong")
        self.set_password(new, keep_session=keep_session)

    def set_password(self, new: str, keep_session: str | None = None) -> None:
        """Set the admin password without checking the old one (CLI reset).

        Raises:
            ValueError: If the password breaks the length rules or no admin exists.
        """
        validate_password(new)
        if not self._store.admin_set_password(hash_password(new)):
            raise ValueError("no admin account exists")
        removed = self._store.auth_session_delete_all(except_hash=keep_session)
        self._store.journal_add("settings", "password", "admin password changed")
        logger.info("Admin password changed; %d other session(s) invalidated", removed)

    def create_api_key(self, name: str, scope: Scope, expires: float | None) -> tuple[str, int]:
        """Issue an API key.

        Returns:
            The plaintext key (shown once) and its id.
        """
        key = API_KEY_PREFIX + secrets.token_urlsafe(32)
        key_id = self._store.api_key_create(name, _sha256(key), scope, expires)
        self._store.journal_add("settings", "api_key", f"API key {name!r} ({scope}) created")
        return key, key_id

    def revoke_api_key(self, key_id: int) -> bool:
        """Revoke an API key."""
        ok = self._store.api_key_revoke(key_id)
        if ok:
            self._store.journal_add("settings", "api_key", f"API key #{key_id} revoked")
        return ok

    def api_key(self, token: str) -> ApiKey | None:
        """Authenticate a Bearer token against the stored key hashes."""
        if not token.startswith(API_KEY_PREFIX):
            return None
        digest = _sha256(token)
        row = self._store.api_key_by_hash(digest)
        now = time.time()
        if (
            not row
            or not hmac.compare_digest(row["key_hash"], digest)
            or row["revoked"] is not None
            or (row["expires"] is not None and row["expires"] <= now)
        ):
            return None
        if row["last_used"] is None or now - row["last_used"] >= _TOUCH_INTERVAL:
            self._store.api_key_touch(row["id"], now)
        return ApiKey(row["id"], row["name"], row["scope"])
