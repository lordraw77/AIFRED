"""Filter for text the agent wants to persist (spec 8.1, "Scanning").

Every write to memory or skills passes :func:`scan` before it is accepted. It
rejects prompt-injection patterns, exfiltration attempts and invisible Unicode.
This is a heuristic guard against the model being talked into storing hostile
instructions, not a complete defence.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

_INJECTION = [
    (r"ignore\s+(?:all\s+|any\s+)?(?:the\s+)?(?:previous|prior|above|earlier)\s+(?:instructions?|prompts?|rules?|messages?)", "prompt-injection phrase"),
    (r"disregard\s+(?:all\s+|any\s+)?(?:the\s+)?(?:previous|prior|above|earlier|system)\s+(?:instructions?|prompts?|rules?)", "prompt-injection phrase"),
    (r"forget\s+(?:all\s+|everything\s+)?(?:your|the)\s+(?:previous\s+|prior\s+)?(?:instructions?|rules?|guidelines?)", "prompt-injection phrase"),
    (r"you\s+are\s+now\s+(?:in\s+)?(?:developer|dan|jailbreak|unrestricted)", "persona override"),
    (r"(?:^|\n)\s*(?:system|assistant|developer)\s*:\s*\S", "fake role marker"),
    (r"<\s*/?\s*(?:system|im_start|im_end|\|im_start\||\|im_end\|)\s*>", "fake role marker"),
    (r"\[/?(?:INST|SYS)\]|<<\s*/?SYS\s*>>", "fake role marker"),
    (r"(?:reveal|print|show|output|repeat)\s+(?:your|the)\s+(?:system\s+prompt|hidden\s+instructions|instructions\s+above)", "prompt-extraction request"),
    (r"(?:do\s+not|don't|never)\s+(?:tell|inform|mention\s+(?:this\s+)?to)\s+the\s+user", "concealment instruction"),
]
_EXFIL = [
    (r"(?:curl|wget)\b[^\n|;]*\|\s*(?:sudo\s+)?(?:ba|z|da)?sh\b", "pipe to shell"),
    (r"https?://[^\s]*[?&](?:api[_-]?key|token|secret|password|passwd|auth|key|session)=[^\s&]{6,}", "URL with secret-looking parameter"),
    (r"\b(?:send|post|upload|exfiltrate|forward)\b[^\n]{0,60}\b(?:api[\s_-]?keys?|passwords?|secrets?|credentials|tokens?|\.env|private\s+keys?)\b[^\n]{0,60}\bhttps?://", "exfiltration instruction"),
    (r"[A-Za-z0-9+/]{200,}={0,2}", "long base64 blob"),
]
_COMPILED = [(re.compile(p, re.IGNORECASE), why) for p, why in _INJECTION + _EXFIL]

# categories: Cf (format, includes zero-width and bidi controls), Co (private use), Cs (surrogates)
_INVISIBLE_CATEGORIES = {"Cf", "Co", "Cs"}
_ALLOWED_CONTROL = {"\n", "\t"}


@dataclass(frozen=True)
class Finding:
    """Why a text was rejected.

    Attributes:
        reason: Short category.
        detail: Fragment that matched (truncated).
    """

    reason: str
    detail: str

    def __str__(self) -> str:
        return f"{self.reason}: {self.detail!r}"


def scan(text: str) -> list[Finding]:
    """Return every reason the text should not be stored (empty when it is clean)."""
    findings: list[Finding] = []
    for ch in text:
        category = unicodedata.category(ch)
        if category in _INVISIBLE_CATEGORIES or (category == "Cc" and ch not in _ALLOWED_CONTROL and ch != "\r"):
            findings.append(Finding("invisible character", f"U+{ord(ch):04X}"))
            break
    for pattern, why in _COMPILED:
        if match := pattern.search(text):
            findings.append(Finding(why, match.group(0).strip()[:60]))
    return findings
