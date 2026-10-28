"""A graph view of the memory entries.

The files stay the source of truth. An entry names the things it is about with double brackets
(``[[Marco]] works at [[Acme]]``); each name is a node and two names in one entry are joined by an edge that carries
the entry. Entries written before the convention, or without brackets, are linked to a name that other entries
already use when they mention it in plain words.
"""

from __future__ import annotations

import re
from typing import Any

MENTION = re.compile(r"\[\[([^\[\]\n|]{1,60})\]\]")
MAX_PER_ENTRY = 8
MIN_AUTO_LENGTH = 3


def _key(name: str) -> str:
    return " ".join(name.lower().split())


def entities(entry: str) -> list[str]:
    """Names written in double brackets in one entry, in order, without repeats."""
    seen: dict[str, str] = {}
    for found in MENTION.findall(entry):
        name = " ".join(found.split())
        if name:
            seen.setdefault(_key(name), name)
    return list(seen.values())


def plain(entry: str) -> str:
    """The entry without the brackets."""
    return MENTION.sub(lambda m: " ".join(m.group(1).split()), entry)


def build(entries: dict[str, list[str]]) -> dict[str, Any]:
    """Nodes, edges and the entries that mention nothing.

    Args:
        entries: Entries by target (``memory`` and ``user``).

    Returns:
        ``nodes`` (id, label, count, targets, entries), ``edges`` (source, target, weight, entries) and ``unlinked``.
    """
    labels: dict[str, str] = {}
    for rows in entries.values():
        for entry in rows:
            for name in entities(entry):
                labels.setdefault(_key(name), name)
    autos = {k: re.compile(rf"(?<!\w){re.escape(k)}(?!\w)", re.IGNORECASE) for k in labels if len(k) >= MIN_AUTO_LENGTH}

    nodes: dict[str, dict[str, Any]] = {}
    edges: dict[tuple[str, str], dict[str, Any]] = {}
    unlinked: list[dict[str, str]] = []
    for target, rows in entries.items():
        for entry in rows:
            keys = [_key(n) for n in entities(entry)]
            text = plain(entry)
            keys += [k for k, rx in autos.items() if k not in keys and rx.search(text)]
            keys = keys[:MAX_PER_ENTRY]
            item = {"target": target, "text": entry}
            if not keys:
                unlinked.append(item)
                continue
            for k in keys:
                node = nodes.setdefault(k, {"id": k, "label": labels[k], "count": 0, "targets": [], "entries": []})
                node["count"] += 1
                if target not in node["targets"]:
                    node["targets"].append(target)
                node["entries"].append(item)
            for i, a in enumerate(keys):
                for b in keys[i + 1:]:
                    edge = edges.setdefault(tuple(sorted((a, b))), {"source": min(a, b), "target": max(a, b), "weight": 0, "entries": []})  # type: ignore[arg-type]
                    edge["weight"] += 1
                    edge["entries"].append(item)
    return {
        "nodes": sorted(nodes.values(), key=lambda n: (-n["count"], n["label"].lower())),
        "edges": sorted(edges.values(), key=lambda e: (-e["weight"], e["source"], e["target"])),
        "unlinked": unlinked,
    }
