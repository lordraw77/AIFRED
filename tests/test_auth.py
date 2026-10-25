"""Tests for aifred.auth."""

from __future__ import annotations

import pytest

from aifred import auth
from aifred.auth import AuthError, AuthService, LoginRateLimiter, RateLimited, hash_password, verify_password
from aifred.store import Store

PASSWORD = "a long enough password"


@pytest.fixture
def service(store: Store) -> AuthService:
    svc = AuthService(store)
    svc.bootstrap_admin("admin", PASSWORD)
    return svc


def test_hash_and_verify() -> None:
    stored = hash_password(PASSWORD)
    assert stored.startswith("scrypt$32768$8$1$")
    assert PASSWORD not in stored
    assert verify_password(PASSWORD, stored)
    assert not verify_password(PASSWORD + "x", stored)
    assert hash_password(PASSWORD) != stored  # random salt
    assert not verify_password(PASSWORD, "garbage")
    assert not verify_password(PASSWORD, "bcrypt$1$2$3$4$5")


def test_bootstrap_rules(store: Store, caplog: pytest.LogCaptureFixture) -> None:
    svc = AuthService(store)
    with pytest.raises(ValueError, match="No admin"):
        svc.bootstrap_admin(None, None)
    with pytest.raises(ValueError, match="at least 12"):
        svc.bootstrap_admin("admin", "short")
    svc.bootstrap_admin("admin", PASSWORD)
    assert svc.username == "admin"

    svc.bootstrap_admin("other", "another password!!")  # ignored: admin exists
    assert svc.username == "admin"
    assert "AIFRED_ADMIN_PASSWORD is ignored" in caplog.text
    assert verify_password(PASSWORD, store.admin_get()["password_hash"])  # type: ignore[index]


def test_login_and_session(service: AuthService, store: Store) -> None:
    token, session = service.login("admin", PASSWORD, "10.0.0.1", "pytest")
    assert service.session(token) == session
    assert service.session("wrong") is None
    assert service.session("") is None
    assert token not in str(store.auth_session_list(0))
    service.logout(session.token_hash)
    assert service.session(token) is None


def test_wrong_credentials(service: AuthService, caplog: pytest.LogCaptureFixture) -> None:
    with pytest.raises(AuthError):
        service.login("admin", "wrong password!!", "1.1.1.1", None)
    with pytest.raises(AuthError):
        service.login("root", PASSWORD, "1.1.1.2", None)
    assert "wrong password" not in caplog.text


def test_session_expiry_and_sliding(service: AuthService, store: Store, monkeypatch: pytest.MonkeyPatch) -> None:
    token, session = service.login("admin", PASSWORD, None, None)
    now = session.expires - auth.SESSION_TTL
    monkeypatch.setattr(auth.time, "time", lambda: now + 3600)
    refreshed = service.session(token)
    assert refreshed is not None and refreshed.expires == pytest.approx(now + 3600 + auth.SESSION_TTL)
    monkeypatch.setattr(auth.time, "time", lambda: now + 3600 + auth.SESSION_TTL + 1)
    assert service.session(token) is None


def test_rate_limiter_lockout_and_backoff() -> None:
    limiter = LoginRateLimiter(limit=5, window=60, base_lockout=60, max_lockout=900)
    keys = ["ip:x"]
    for _ in range(4):
        limiter.failure(keys, now=100)
    assert limiter.check(keys, now=100) == 0
    limiter.failure(keys, now=100)
    assert limiter.check(keys, now=100) == 60
    assert limiter.check(keys, now=161) == 0
    for _ in range(5):
        limiter.failure(keys, now=200)
    assert limiter.check(keys, now=200) == 120  # doubled
    limiter.success(keys)
    assert limiter.check(keys, now=200) == 0


def test_rate_limiter_window_expires() -> None:
    limiter = LoginRateLimiter(limit=3, window=60)
    limiter.failure(["k"], now=0)
    limiter.failure(["k"], now=1)
    limiter.failure(["k"], now=100)  # first two are outside the window
    assert limiter.check(["k"], now=100) == 0


def test_login_rate_limited_per_username(service: AuthService) -> None:
    for i in range(5):
        with pytest.raises(AuthError):
            service.login("admin", "wrong password!!", f"10.0.0.{i}", None)
    with pytest.raises(RateLimited):
        service.login("admin", PASSWORD, "10.9.9.9", None)


def test_change_password_invalidates_other_sessions(service: AuthService) -> None:
    t1, s1 = service.login("admin", PASSWORD, None, None)
    t2, _ = service.login("admin", PASSWORD, None, None)
    with pytest.raises(AuthError):
        service.change_password("wrong password!!", "brand new password", keep_session=s1.token_hash)
    with pytest.raises(ValueError):
        service.change_password(PASSWORD, "short", keep_session=s1.token_hash)
    service.change_password(PASSWORD, "brand new password", keep_session=s1.token_hash)
    assert service.session(t1) is not None
    assert service.session(t2) is None
    service.login("admin", "brand new password", None, None)


def test_revoke_session_by_id(service: AuthService) -> None:
    token, session = service.login("admin", PASSWORD, None, None)
    [listed] = service.sessions()
    assert listed["id"] == session.token_hash[:16]
    assert not service.revoke_session("short")
    assert service.revoke_session(session.token_hash[:16])
    assert service.session(token) is None


def test_api_keys(service: AuthService, store: Store, monkeypatch: pytest.MonkeyPatch) -> None:
    key, key_id = service.create_api_key("bot", "chat", None)
    assert key.startswith("aif_")
    assert key not in str(store.api_key_list())
    assert service.api_key(key) == auth.ApiKey(key_id, "bot", "chat")
    assert service.api_key("aif_wrong") is None
    assert service.api_key("not-a-key") is None
    assert service.revoke_api_key(key_id)
    assert not service.revoke_api_key(key_id)
    assert service.api_key(key) is None

    expiring, _ = service.create_api_key("tmp", "admin", auth.time.time() + 10)
    assert service.api_key(expiring) is not None
    real = auth.time.time()
    monkeypatch.setattr(auth.time, "time", lambda: real + 11)
    assert service.api_key(expiring) is None
