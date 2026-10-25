"""Tests for aifred.memory."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from aifred.memory import LIMITS, SEPARATOR, Memory, MemoryError_


@pytest.fixture
def mem(tmp_path: Path) -> Memory:
    return Memory(tmp_path)


def test_limits_match_the_spec() -> None:
    assert LIMITS == {"memory": 2200, "user": 1375}


def test_add_and_snapshot(mem: Memory, tmp_path: Path) -> None:
    mem.add("user", "Prefers tea")
    mem.add("user", "Lives in Bologna")
    snap = mem.snapshot("user")
    assert snap.entries == ["Prefers tea", "Lives in Bologna"]
    assert snap.used == len(SEPARATOR.join(snap.entries)) and snap.limit == 1375
    assert snap.counter == f"{snap.used:,} / 1,375"
    assert snap.render() == "- Prefers tea\n- Lives in Bologna"
    assert (tmp_path / "memory" / "USER.md").read_text() == f"Prefers tea{SEPARATOR}Lives in Bologna"
    assert (tmp_path / "memory" / "USER.md").stat().st_mode & 0o777 == 0o600
    assert mem.snapshot("memory").render() == "(empty)"


def test_duplicate_entry_is_not_added_twice(mem: Memory) -> None:
    mem.add("memory", "Same fact")
    mem.add("memory", "Same fact")
    assert mem.snapshot("memory").entries == ["Same fact"]


def test_separator_cannot_be_smuggled_into_an_entry(mem: Memory) -> None:
    stored = mem.add("memory", "first\n§\nsecond § third")
    assert "§" not in stored
    assert len(mem.snapshot("memory").entries) == 1


def test_empty_content_and_unknown_target(mem: Memory) -> None:
    for bad in (None, "", "   \n"):
        with pytest.raises(MemoryError_, match="content is required"):
            mem.add("memory", bad)
    with pytest.raises(MemoryError_, match="unknown target"):
        mem.add("diary", "x")


def test_limit_is_enforced_with_a_helpful_message(mem: Memory) -> None:
    mem.add("user", "a" * 1300)
    with pytest.raises(MemoryError_, match=r"full.*Condense or remove"):
        mem.add("user", "b" * 100)
    assert mem.snapshot("user").entries == ["a" * 1300]
    mem.add("user", "c" * 60)  # 1300 + 3 + 60 = 1363, still fits
    assert mem.snapshot("user").used == 1363


def test_replace_and_remove_by_unique_substring(mem: Memory) -> None:
    mem.add("memory", "Server alpha runs Debian")
    mem.add("memory", "Server beta runs Rocky")
    old, new = mem.replace("memory", "ALPHA", "Server alpha runs Ubuntu")
    assert (old, new) == ("Server alpha runs Debian", "Server alpha runs Ubuntu")
    assert mem.remove("memory", "beta") == "Server beta runs Rocky"
    assert mem.snapshot("memory").entries == ["Server alpha runs Ubuntu"]


def test_ambiguous_and_missing_matches(mem: Memory) -> None:
    mem.add("memory", "Server alpha")
    mem.add("memory", "Server beta")
    with pytest.raises(MemoryError_, match="2 entries contain"):
        mem.remove("memory", "Server")
    with pytest.raises(MemoryError_, match="no entry contains"):
        mem.replace("memory", "gamma", "x")
    for bad in (None, "  "):
        with pytest.raises(MemoryError_, match="'match' is required"):
            mem.remove("memory", bad)
    assert len(mem.snapshot("memory").entries) == 2


def test_replace_cannot_exceed_the_limit(mem: Memory) -> None:
    mem.add("user", "short")
    with pytest.raises(MemoryError_, match="full"):
        mem.replace("user", "short", "x" * 2000)
    assert mem.snapshot("user").entries == ["short"]


def test_remove_exact(mem: Memory) -> None:
    mem.add("memory", "keep")
    mem.add("memory", "drop")
    assert mem.remove_exact("memory", "drop") and not mem.remove_exact("memory", "drop")
    assert mem.snapshot("memory").entries == ["keep"]


def test_two_processes_never_overwrite_each_other(tmp_path: Path) -> None:
    """Each Memory has its own fds, like `serve` and `cli`; flock must serialise their read-modify-write."""
    a, b = Memory(tmp_path), Memory(tmp_path)
    errors: list[Exception] = []

    def work(mem: Memory, tag: str) -> None:
        try:
            for i in range(25):
                mem.add("memory", f"{tag}{i}")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(m, t)) for m, t in ((a, "a"), (b, "b"))]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors
    entries = Memory(tmp_path).snapshot("memory").entries
    assert len(entries) == 50 and {f"a{i}" for i in range(25)} | {f"b{i}" for i in range(25)} == set(entries)
