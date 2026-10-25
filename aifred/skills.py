"""File-based skills (spec section 8).

A skill is ``data/skills/<name>/SKILL.md`` with ``name`` and ``description``
frontmatter and a Markdown body. Only name and description go into the prompt;
the body is loaded on demand with ``skill_view``.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from aifred.memory import Memory

logger = logging.getLogger(__name__)

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,48}$")
MAX_DESCRIPTION = 300
MAX_BODY = 20_000


class SkillError(Exception):
    """A skill operation was refused; the message is meant for the model."""


@dataclass(frozen=True)
class Skill:
    """A parsed skill.

    Attributes:
        name: Skill name (the directory name).
        description: One-line description.
        body: Markdown body.
    """

    name: str
    description: str
    body: str


def render(skill: Skill) -> str:
    """Serialise a skill to ``SKILL.md`` text."""
    return f"---\nname: {skill.name}\ndescription: {skill.description}\n---\n\n{skill.body.strip()}\n"


def parse(text: str) -> tuple[dict[str, str], str]:
    """Split frontmatter from the body (a minimal ``key: value`` parser, no YAML dependency)."""
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end < 0:
        return {}, text
    meta: dict[str, str] = {}
    for line in text[4:end].splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip():
            meta[key.strip().lower()] = value.strip()
    return meta, text[end + 4 :].lstrip("\n")


class SkillStore:
    """The skills directory.

    Args:
        data_dir: Data directory.
        memory: Provides the shared write lock.
    """

    def __init__(self, data_dir: Path, memory: Memory) -> None:
        self._root = data_dir / "skills"
        self._memory = memory
        self._root.mkdir(parents=True, exist_ok=True)

    def _dir(self, name: str) -> Path:
        if not NAME_RE.match(name):
            raise SkillError("invalid skill name: use lowercase letters, digits and hyphens (1-49 characters)")
        path = (self._root / name).resolve()
        if path.parent != self._root.resolve():
            raise SkillError("invalid skill name")
        return path

    def get(self, name: str) -> Skill:
        """Load a skill.

        Raises:
            SkillError: If the name is invalid or the skill does not exist.
        """
        path = self._dir(name) / "SKILL.md"
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise SkillError(f"no skill named {name!r}") from None
        meta, body = parse(text)
        return Skill(name, meta.get("description", ""), body.strip())

    def index(self) -> list[tuple[str, str]]:
        """Return ``(name, description)`` for every valid skill, sorted by name."""
        out = []
        for entry in sorted(self._root.iterdir()) if self._root.exists() else []:
            if not entry.is_dir() or not NAME_RE.match(entry.name) or not (entry / "SKILL.md").is_file():
                continue
            try:
                out.append((entry.name, self.get(entry.name).description))
            except (SkillError, OSError, UnicodeDecodeError):
                logger.warning("Skipping unreadable skill %s", entry.name)
        return out

    def save(self, name: str, description: str, body: str) -> tuple[Skill, Skill | None]:
        """Create or overwrite a skill.

        Returns:
            The new skill and the previous one (``None`` when new).

        Raises:
            SkillError: On invalid input.
        """
        path = self._dir(name)
        description = " ".join(description.split())
        if not description:
            raise SkillError("description is required")
        if len(description) > MAX_DESCRIPTION:
            raise SkillError(f"description is too long ({len(description)} > {MAX_DESCRIPTION} characters)")
        if not body.strip():
            raise SkillError("body is required")
        if len(body) > MAX_BODY:
            raise SkillError(f"body is too long ({len(body):,} > {MAX_BODY:,} characters)")
        skill = Skill(name, description, body.strip())
        with self._memory.locked():
            try:
                previous = self.get(name)
            except SkillError:
                previous = None
            path.mkdir(mode=0o700, exist_ok=True)
            target = path / "SKILL.md"
            tmp = path / "SKILL.md.tmp"
            tmp.write_text(render(skill), encoding="utf-8")
            os.chmod(tmp, 0o600)
            os.replace(tmp, target)
        return skill, previous

    def restore(self, name: str, previous: Skill | None) -> None:
        """Undo a save: rewrite the previous version, or delete a skill that was new."""
        if previous is None:
            self.delete(name)
        else:
            self.save(previous.name, previous.description, previous.body)

    def delete(self, name: str) -> bool:
        """Delete a skill (manual removal; the agent has no tool for this)."""
        path = self._dir(name)
        with self._memory.locked():
            if not path.is_dir():
                return False
            shutil.rmtree(path)
        return True
