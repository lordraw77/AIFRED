"""Guarded writes to memory and skills: the one place that screens, applies, journals and queues them.

Used by the ``memory`` and ``skill_save`` tools and by the ``/approve`` command, so a write behaves the
same whether the model makes it directly or the admin approves it later.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from aifred.memory import MemoryError_
from aifred.scanner import scan
from aifred.skills import SkillError

if TYPE_CHECKING:
    from aifred.app import Services


class WriteError(Exception):
    """A write was refused; the message is meant for the model or the admin."""


def approval_required(services: Services, kind: str) -> bool:
    """Whether ``WRITE_APPROVAL`` asks for the admin's approval before a ``memory`` or ``skills`` write."""
    mode = services.config.get("WRITE_APPROVAL")
    return bool(mode == "all" or mode == kind)


def screen(services: Services, kind: str, action: str, text: str, label: str, session_id: str | None) -> None:
    """Reject text that trips the scanner; the rejection is journaled as ``blocked``.

    Raises:
        WriteError: With the reasons.
    """
    if findings := scan(text):
        why = "; ".join(str(f) for f in findings)
        services.store.journal_add(kind, action, f"blocked: {label}", session_id, "blocked", {"reasons": why})
        raise WriteError(f"rejected by the safety filter ({why}). Rephrase the content as plain facts.")


def queue_write(services: Services, kind: str, payload: dict[str, Any], session_id: str | None, action: str, summary: str) -> int:
    """Put a write in the approval queue and journal it as ``pending``; returns the queue id."""
    wid = services.store.pending_add(kind, payload, session_id)
    services.store.journal_add(kind, action, f"queued #{wid}: {summary[:80]}", session_id, "pending", {"pending_id": wid})
    return wid


def apply_memory(services: Services, payload: dict[str, Any], session_id: str | None) -> str:
    """Add, replace or remove a memory entry and journal it with what is needed to undo it.

    Raises:
        WriteError: If the memory refuses (full, ambiguous match, empty content).
    """
    mem, action, target = services.memory, payload["action"], payload.get("target", "memory")
    try:
        if action == "add":
            entry = mem.add(target, payload.get("content"))
            data, summary = {"target": target, "entry": entry}, entry
        elif action == "replace":
            old, entry = mem.replace(target, payload.get("match"), payload.get("content"))
            data, summary = {"target": target, "entry": entry, "old": old}, f"{old}  →  {entry}"
        else:
            old = mem.remove(target, payload.get("match"))
            data, summary = {"target": target, "old": old}, f"removed: {old}"
    except MemoryError_ as exc:
        raise WriteError(str(exc)) from None
    services.store.journal_add("memory", action, summary[:300], session_id, "applied", data)
    snap = mem.snapshot(target)
    return f"Done. {target} memory now uses {snap.used:,} of {snap.limit:,} characters."


def apply_skill(services: Services, payload: dict[str, Any], session_id: str | None) -> str:
    """Create or overwrite a skill and journal it with the previous version.

    Raises:
        WriteError: If the skill is invalid.
    """
    try:
        skill, previous = services.skills.save(payload["name"], payload["description"], payload["body"])
    except SkillError as exc:
        raise WriteError(str(exc)) from None
    previous_data = None if previous is None else {"description": previous.description, "body": previous.body}
    services.store.journal_add(
        "skill", "update" if previous else "create", f"{skill.name}: {skill.description}"[:300], session_id,
        "applied", {"name": skill.name, "previous": previous_data},
    )
    return f"Skill {skill.name!r} {'updated' if previous else 'created'}."


def apply_pending(services: Services, write_id: int) -> str:
    """Apply a queued write after the admin approved it (the scanner runs again).

    Raises:
        WriteError: If it does not exist, was already decided, or the write is now refused (it stays queued).
    """
    row = services.store.pending_get(write_id)
    if row is None:
        raise WriteError(f"no pending write #{write_id}")
    if row["status"] != "pending":
        raise WriteError(f"#{write_id} was already {row['status']}")
    payload, kind, session_id = row["payload"], row["kind"], row["session_id"]
    if kind == "memory":
        if payload.get("action") in ("add", "replace"):
            screen(services, "memory", payload["action"], payload.get("content") or "", (payload.get("content") or "")[:80], session_id)
        message = apply_memory(services, payload, session_id)
    elif kind == "skill":
        screen(services, "skill", "save", f"{payload['description']}\n{payload['body']}", payload["name"], session_id)
        message = apply_skill(services, payload, session_id)
    else:
        raise WriteError(f"cannot apply a write of kind {kind!r}")
    services.store.pending_decide(write_id, "approved")
    _mark_journal(services, write_id, "approved")
    return message


def reject_pending(services: Services, write_id: int) -> None:
    """Discard a queued write.

    Raises:
        WriteError: If it does not exist or was already decided.
    """
    row = services.store.pending_get(write_id)
    if row is None:
        raise WriteError(f"no pending write #{write_id}")
    if not services.store.pending_decide(write_id, "rejected"):
        raise WriteError(f"#{write_id} was already {row['status']}")
    _mark_journal(services, write_id, "rejected")


def _mark_journal(services: Services, write_id: int, status: str) -> None:
    for entry in services.store.journal_list(limit=200):
        if entry["status"] == "pending" and (entry["data"] or {}).get("pending_id") == write_id:
            services.store.journal_set_status(entry["id"], status)
            return


def describe_pending(row: dict[str, Any]) -> str:
    """One line for ``/pending``."""
    p = row["payload"]
    if row["kind"] == "memory":
        what = f"memory {p.get('action')} ({p.get('target', 'memory')}): {(p.get('content') or p.get('match') or '')[:100]}"
    else:
        what = f"skill {p.get('name')}: {p.get('description', '')[:100]}"
    return f"#{row['id']} {what}"


__all__ = ["WriteError", "apply_memory", "apply_pending", "apply_skill", "approval_required", "describe_pending", "queue_write", "reject_pending", "screen"]
