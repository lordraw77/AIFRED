"""Optional checks against a real OpenBao/Vault server.

Skipped unless these variables are set (see docs/adr.md for how to start a throw-away server):
AIFRED_TEST_OPENBAO_ADDR, AIFRED_TEST_OPENBAO_ROLE_ID, AIFRED_TEST_OPENBAO_SECRET_ID,
and, for the seal and revoke checks, AIFRED_TEST_OPENBAO_ROOT_TOKEN and AIFRED_TEST_OPENBAO_UNSEAL_KEY.
"""

from __future__ import annotations

import os

import httpx
import pytest

from aifred.store import Store
from aifred.vault import OpenBaoBox, Vault, VaultError, VaultUnavailable

ADDR = os.environ.get("AIFRED_TEST_OPENBAO_ADDR")
ROLE = os.environ.get("AIFRED_TEST_OPENBAO_ROLE_ID", "")
SECRET = os.environ.get("AIFRED_TEST_OPENBAO_SECRET_ID", "")
ROOT = os.environ.get("AIFRED_TEST_OPENBAO_ROOT_TOKEN", "")
UNSEAL = os.environ.get("AIFRED_TEST_OPENBAO_UNSEAL_KEY", "")

pytestmark = pytest.mark.skipif(not (ADDR and ROLE and SECRET), reason="no real OpenBao configured")


def box() -> OpenBaoBox:
    assert ADDR
    return OpenBaoBox(ADDR, role_id=ROLE, secret_id=SECRET)


def test_round_trip_and_name_binding() -> None:
    b = box()
    blob = b.encrypt("gsk_live_secret_value", "provider_key:1")
    assert blob.startswith(b"vault:v1:") and b.health() == (True, "ready")
    assert b.decrypt(blob, "provider_key:1") == "gsk_live_secret_value"
    with pytest.raises(VaultError, match="different name"):
        b.decrypt(blob, "provider_key:2")
    with pytest.raises(VaultError):
        b.decrypt(b"vault:v1:AAAA", "x")


def test_unicode_and_large_values() -> None:
    b = box()
    value = "chiave-è-àòù-🙂-" + "x" * 5000
    assert b.decrypt(b.encrypt(value, "n"), "n") == value


def test_database_never_holds_plaintext(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = Store(tmp_path / "db.sqlite")
    Vault(store, box()).set("s", "LIVE-PLAINTEXT-MUST-NOT-APPEAR")
    store.close()
    for name in ("db.sqlite", "db.sqlite-wal"):  # the WAL is folded into the database on close
        if (tmp_path / name).exists():
            assert b"LIVE-PLAINTEXT" not in (tmp_path / name).read_bytes()


@pytest.mark.skipif(not ROOT, reason="needs the root token to revoke")
def test_revoked_token_triggers_a_new_login() -> None:
    b = box()
    blob = b.encrypt("v", "n")
    token = b._token
    r = httpx.post(f"{ADDR}/v1/auth/token/revoke", headers={"X-Vault-Token": ROOT}, json={"token": token})
    assert r.status_code in (200, 204)
    assert b.decrypt(blob, "n") == "v"
    assert b._token != token


@pytest.mark.skipif(not (ROOT and UNSEAL), reason="needs the root token and the unseal key")
def test_seal_and_unseal() -> None:
    b = box()
    blob = b.encrypt("v", "n")
    httpx.put(f"{ADDR}/v1/sys/seal", headers={"X-Vault-Token": ROOT}).raise_for_status()
    try:
        assert b.health() == (False, "sealed")
        with pytest.raises(VaultUnavailable, match="sealed"):
            b.decrypt(blob, "n")
        with pytest.raises(VaultUnavailable, match="sealed"):
            b.encrypt("v", "n")
    finally:
        httpx.put(f"{ADDR}/v1/sys/unseal", json={"key": UNSEAL}).raise_for_status()
    assert b.health() == (True, "ready")
    assert b.decrypt(blob, "n") == "v"


def test_wrong_credentials_and_missing_key() -> None:
    assert ADDR
    with pytest.raises(VaultUnavailable, match="rejected the AppRole login"):
        OpenBaoBox(ADDR, role_id=ROLE, secret_id="not-the-secret-id").encrypt("v", "n")
    with pytest.raises(VaultUnavailable, match="denied access"):
        OpenBaoBox(ADDR, role_id=ROLE, secret_id=SECRET, key="ghost").encrypt("v", "n")
