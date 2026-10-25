"""Tests for aifred.vault."""

from __future__ import annotations

from pathlib import Path

import pytest

from aifred.store import Store
from aifred.vault import SecretBox, Vault, VaultError, load_or_create_key


def test_key_created_with_restricted_mode(tmp_path: Path) -> None:
    path = tmp_path / ".secret.key"
    key = load_or_create_key(path)
    assert len(key) == 32
    assert path.stat().st_mode & 0o777 == 0o600
    assert load_or_create_key(path) == key


def test_invalid_key_file(tmp_path: Path) -> None:
    path = tmp_path / ".secret.key"
    path.write_bytes(b"short")
    with pytest.raises(VaultError):
        load_or_create_key(path)


def test_round_trip_and_no_plaintext_in_db(tmp_path: Path, store: Store) -> None:
    vault = Vault(store, SecretBox(load_or_create_key(tmp_path / "k")))
    vault.set("provider:groq", "gsk_supersecretvalue")
    assert vault.get("provider:groq") == "gsk_supersecretvalue"
    assert vault.last4("provider:groq") == "alue"
    assert b"supersecret" not in store.secret_get("provider:groq")  # type: ignore[operator]
    assert b"supersecret" not in Path(store.path).read_bytes()
    assert vault.delete("provider:groq")
    assert vault.get("provider:groq") is None
    assert not vault.exists("provider:groq")


def test_ciphertext_bound_to_name(tmp_path: Path, store: Store) -> None:
    box = SecretBox(load_or_create_key(tmp_path / "k"))
    blob = box.encrypt("value", "a")
    with pytest.raises(VaultError):
        box.decrypt(blob, "b")
    store.secret_set("b", blob)
    assert Vault(store, box).get("b") is None


def test_wrong_key_returns_none(tmp_path: Path, store: Store) -> None:
    Vault(store, SecretBox(load_or_create_key(tmp_path / "k1"))).set("s", "value")
    assert Vault(store, SecretBox(load_or_create_key(tmp_path / "k2"))).get("s") is None


def test_short_secret_has_no_last4(tmp_path: Path, store: Store) -> None:
    vault = Vault(store, SecretBox(load_or_create_key(tmp_path / "k")))
    vault.set("s", "abc")
    assert vault.last4("s") is None
