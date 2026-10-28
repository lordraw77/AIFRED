"""The library: answers and results the user chose to keep, found again by the assistant when they are asked for.

Items are Markdown with a title, tags and the request that produced them. Chat and agents read them with the
``library_search`` and ``library_get`` tools; a saved item can be written again from its request (``regenerate``),
which only returns a draft until the user accepts it.
"""

from __future__ import annotations

import asyncio
import logging
import re
import sqlite3
import uuid
from typing import TYPE_CHECKING, Any

from aifred.agent import DoneEvent, ErrorEvent

if TYPE_CHECKING:
    from aifred.app import Services

logger = logging.getLogger(__name__)

TITLE_MAX = 120
PROMPT_MAX = 4000
TAG_MAX = 30
TAGS_MAX = 8
SOURCES = ("manual", "chat", "agent")
REGENERATE_NOTE = (
    "\n\n(The user asked for this answer again: write it afresh from the request above. "
    "Do not read or copy the library item being rewritten.)"
)


class LibraryError(Exception):
    """A library operation was refused; the message is meant for the user or the model."""

    def __init__(self, message: str, code: str = "invalid") -> None:
        super().__init__(message)
        self.code = code


def tags_of(raw: list[str] | str | None) -> list[str]:
    """Clean a list (or comma-separated text) of tags: lower case, words joined with ``-``, no repeats.

    Raises:
        LibraryError: If there are too many tags or one is too long.
    """
    items = raw.split(",") if isinstance(raw, str) else list(raw or [])
    tags: dict[str, None] = {}
    for item in items:
        tag = re.sub(r"[\s_]+", "-", str(item).strip().lower()).strip("-")
        if not tag:
            continue
        if len(tag) > TAG_MAX or not re.fullmatch(r"[\w-]+", tag):
            raise LibraryError(f"a tag is letters, digits and '-', up to {TAG_MAX} characters: {tag[:40]!r}")
        tags[tag] = None
    if len(tags) > TAGS_MAX:
        raise LibraryError(f"at most {TAGS_MAX} tags")
    return list(tags)


class LibraryService:
    """Saves, finds and rewrites library items.

    Args:
        services: The process services.
    """

    def __init__(self, services: Services) -> None:
        self._s = services

    # Reading

    def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """Items matching a request, best first (title, tags, snippet)."""
        return self._s.store.library_search(query, limit)

    def find(self, ref: str | int) -> dict[str, Any] | None:
        """An item by id, or by title (exact first, then a single item whose title contains the text)."""
        store = self._s.store
        if isinstance(ref, int) or str(ref).strip().isdigit():
            return store.library_get(int(ref))
        text = str(ref).strip()
        exact = store.library_by_title(text)
        if exact:
            return exact
        hits = [h for h in store.library_search(text, 5) if text.lower() in h["title"].lower()]
        return store.library_get(hits[0]["id"]) if len(hits) == 1 else None

    def prompt_index(self) -> list[dict[str, Any]]:
        """The titles shown in the system prompt (``LIBRARY_PROMPT_ITEMS`` latest items)."""
        limit = int(self._s.config.get("LIBRARY_PROMPT_ITEMS"))
        return self._s.store.library_titles(limit) if limit > 0 else []

    # Writing

    def _check(self, title: str | None, content: str | None, prompt: str | None) -> None:
        if title is not None:
            if not title.strip() or "\n" in title.strip():
                raise LibraryError("the title is required and fits on one line")
            if len(title.strip()) > TITLE_MAX:
                raise LibraryError(f"the title is at most {TITLE_MAX} characters")
        if content is not None:
            if not content.strip():
                raise LibraryError("the content is required")
            limit = int(self._s.config.get("LIBRARY_ITEM_CHARS"))
            if len(content) > limit:
                raise LibraryError(f"the content is {len(content):,} characters; the limit is {limit:,} (LIBRARY_ITEM_CHARS)", "too_big")
        if prompt is not None and len(prompt) > PROMPT_MAX:
            raise LibraryError(f"the request is at most {PROMPT_MAX} characters")

    def save(
        self, title: str, content: str, *, prompt: str = "", tags: list[str] | str | None = None,
        source: str = "manual", source_ref: str = "", replace: bool = False,
    ) -> tuple[dict[str, Any], bool]:
        """Add an item, or with ``replace`` rewrite the one with the same title (the old text is kept for one undo).

        Returns:
            ``(item, created)``.

        Raises:
            LibraryError: If a field is invalid, the library is full, or the title exists and ``replace`` is off (code ``exists``).
        """
        self._check(title, content, prompt)
        title, content = title.strip(), content.strip()
        clean = tags_of(tags)
        if source not in SOURCES:
            source = "manual"
        store = self._s.store
        existing = store.library_by_title(title)
        if existing:
            if not replace:
                raise LibraryError(f"the library already has {title!r}", "exists")
            values: dict[str, Any] = {"content": content, "tags": clean or existing["tags"]}
            if prompt:
                values["prompt"] = prompt
            store.library_update(existing["id"], values, keep_previous=True)
            created, item_id = False, existing["id"]
        else:
            cap = int(self._s.config.get("LIBRARY_MAX_ITEMS"))
            if store.library_count() >= cap:
                raise LibraryError(f"the library holds {cap} items (LIBRARY_MAX_ITEMS); delete some first", "full")
            try:
                item_id = store.library_add(title, content, prompt, clean, source, source_ref[:200])
            except sqlite3.IntegrityError:
                raise LibraryError(f"the library already has {title!r}", "exists") from None
            created = True
        self._s.agent.reset_all_prompts()
        item = store.library_get(item_id)
        assert item is not None
        return item, created

    def update(self, item_id: int, values: dict[str, Any]) -> dict[str, Any]:
        """Change an item's title, content, request or tags; a changed content keeps the old one for one undo.

        Raises:
            LibraryError: If the item does not exist (code ``missing``), a field is invalid or the title is taken (``exists``).
        """
        store = self._s.store
        item = store.library_get(item_id)
        if item is None:
            raise LibraryError("no such item", "missing")
        self._check(values.get("title"), values.get("content"), values.get("prompt"))
        changes: dict[str, Any] = {}
        if values.get("title") is not None:
            changes["title"] = values["title"].strip()
        if values.get("content") is not None and values["content"].strip() != item["content"]:
            changes["content"] = values["content"].strip()
        if values.get("prompt") is not None:
            changes["prompt"] = values["prompt"]
        if values.get("tags") is not None:
            changes["tags"] = tags_of(values["tags"])
        try:
            store.library_update(item_id, changes, keep_previous="content" in changes)
        except sqlite3.IntegrityError:
            raise LibraryError(f"the library already has {changes.get('title')!r}", "exists") from None
        self._s.agent.reset_all_prompts()
        updated = store.library_get(item_id)
        assert updated is not None
        return updated

    def restore(self, item_id: int) -> dict[str, Any]:
        """Go back to the previous text (and forward again if pressed twice)."""
        if not self._s.store.library_restore(item_id):
            raise LibraryError("there is no earlier version", "missing")
        item = self._s.store.library_get(item_id)
        assert item is not None
        return item

    def delete(self, item_id: int) -> None:
        """Delete an item."""
        if not self._s.store.library_delete(item_id):
            raise LibraryError("no such item", "missing")
        self._s.agent.reset_all_prompts()

    # Regenerating

    async def regenerate(self, item_id: int) -> str:
        """Ask again with the stored request and return the new text. Nothing is saved.

        The run goes through the normal agent (same model route and tools as the web chat) in a throw-away conversation.

        Raises:
            LibraryError: If the item has no stored request, the run fails or takes too long.
        """
        item = await asyncio.to_thread(self._s.store.library_get, item_id)
        if item is None:
            raise LibraryError("no such item", "missing")
        if not item["prompt"].strip():
            raise LibraryError("this item has no request stored; add the request that produced it (Edit) and try again", "no_prompt")
        session_id = f"library:{uuid.uuid4().hex[:12]}"
        timeout = float(self._s.config.get("AGENT_TIMEOUT"))

        async def ask() -> str:
            final = ""
            async for event in self._s.agent.run(session_id, item["prompt"] + REGENERATE_NOTE, "library"):
                if isinstance(event, DoneEvent):
                    final = event.content
                elif isinstance(event, ErrorEvent):
                    raise LibraryError(event.message, "model")
            return final

        try:
            text = await asyncio.wait_for(ask(), timeout)
        except TimeoutError:
            raise LibraryError(f"it took longer than {timeout:.0f} s (AGENT_TIMEOUT)", "timeout") from None
        finally:
            await asyncio.to_thread(self._s.store.delete_session, session_id)
        if not text.strip():
            raise LibraryError("the model produced no answer", "model")
        return text.strip()
