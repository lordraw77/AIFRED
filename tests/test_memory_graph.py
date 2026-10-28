"""Tests for the memory graph, the configurable limits and the memory API."""

from __future__ import annotations

from pathlib import Path

import pytest

from aifred.memory import Memory, MemoryError_
from aifred.memory_graph import build, entities, plain


def test_names_in_double_brackets_are_found_once_in_order() -> None:
    assert entities("[[Marco]] works at [[Acme]], and [[marco]] again; [[ Anna   Rossi ]]") == ["Marco", "Acme", "Anna Rossi"]
    assert entities("no names, [single] and [[]] and [[a|b]]") == []
    assert plain("[[Marco]] works at [[Acme]]") == "Marco works at Acme"


def test_two_names_in_one_entry_are_joined() -> None:
    g = build({"memory": ["[[Marco]] works at [[Acme]]", "[[Acme]] is in [[Bologna]]"], "user": ["[[Marco]] likes tea"]})
    ids = {n["id"]: n for n in g["nodes"]}
    assert set(ids) == {"marco", "acme", "bologna"}
    assert ids["marco"]["count"] == 2 and ids["marco"]["targets"] == ["memory", "user"]
    assert ids["acme"]["count"] == 2 and ids["bologna"]["targets"] == ["memory"]
    edges = {(e["source"], e["target"]): e for e in g["edges"]}
    assert set(edges) == {("acme", "marco"), ("acme", "bologna")}
    assert edges[("acme", "marco")]["entries"] == [{"target": "memory", "text": "[[Marco]] works at [[Acme]]"}]


def test_a_repeated_pair_adds_weight() -> None:
    g = build({"memory": ["[[A1]] and [[B1]] met", "[[B1]] and [[A1]] met again"], "user": []})
    assert len(g["edges"]) == 1 and g["edges"][0]["weight"] == 2


def test_old_entries_join_a_name_other_entries_use() -> None:
    g = build({"memory": ["[[Acme]] is the employer", "Marco works at acme headquarters", "Nothing here", "Acmeism is unrelated"], "user": []})
    acme = next(n for n in g["nodes"] if n["id"] == "acme")
    assert [e["text"] for e in acme["entries"]] == ["[[Acme]] is the employer", "Marco works at acme headquarters"]
    assert [u["text"] for u in g["unlinked"]] == ["Nothing here", "Acmeism is unrelated"]


def test_an_entry_with_many_names_is_capped() -> None:
    names = " ".join(f"[[N{i}]]" for i in range(20))
    g = build({"memory": [names], "user": []})
    assert len(g["nodes"]) == 8 and len(g["edges"]) == 28


def test_empty_memory_has_an_empty_graph() -> None:
    assert build({"memory": [], "user": []}) == {"nodes": [], "edges": [], "unlinked": []}


def test_limits_follow_the_setting_and_apply_at_once(tmp_path: Path) -> None:
    sizes = {"memory": 100, "user": 60}
    mem = Memory(tmp_path, lambda t: sizes[t])
    assert mem.snapshot("user").limit == 60 and mem.snapshot("memory").limit == 100
    with pytest.raises(MemoryError_, match="full"):
        mem.add("user", "x" * 70)
    sizes["user"] = 200
    mem.add("user", "x" * 70)
    sizes["user"] = 50                                   # lowering the limit keeps what is there, refuses more
    assert mem.snapshot("user").entries == ["x" * 70]
    with pytest.raises(MemoryError_, match="full"):
        mem.add("user", "y")
    mem.remove("user", "xxx")
    mem.add("user", "y")


def test_both_limits_are_settings(services) -> None:  # noqa: ANN001
    assert services.memory.snapshot("memory").limit == 2200 and services.memory.snapshot("user").limit == 1375
    services.config.update({"USER_LIMIT": 4000, "MEMORY_LIMIT": 500})
    assert services.memory.snapshot("user").limit == 4000 and services.memory.snapshot("memory").limit == 500
    with pytest.raises(Exception):  # noqa: B017, PT011
        services.config.update({"USER_LIMIT": 10})
