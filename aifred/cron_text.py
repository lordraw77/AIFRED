"""Schedules written as plain sentences, in Italian or English, turned into cron (spec 9.5).

Examples that work: ``ogni mattina alle 6``, ``every morning at 6``, ``dal lunedì al venerdì alle 8:30``,
``every monday and thursday at 9am``, ``ogni 2 ore``, ``il primo del mese alle 8``, ``every month on the 15th at 10``.

The reading is done by rules, not by a model, so it is free, instant, works offline and always gives the same answer.
A sentence that names a frequency but leaves something out ("every morning") is refused with what is missing instead
of guessing a time. Anything that is not a sentence about time is left to the cron parser to judge.
"""

from __future__ import annotations

import re
import unicodedata

HOUR_WORDS = {
    "uno": 1, "una": 1, "due": 2, "tre": 3, "quattro": 4, "cinque": 5, "sei": 6, "sette": 7, "otto": 8, "nove": 9, "dieci": 10,
    "undici": 11, "dodici": 12, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}
DAYS = {
    "lunedi": 1, "lun": 1, "martedi": 2, "mar": 2, "mercoledi": 3, "mer": 3, "giovedi": 4, "gio": 4, "venerdi": 5, "ven": 5,
    "sabato": 6, "sab": 6, "domenica": 0, "dom": 0, "monday": 1, "mon": 1, "tuesday": 2, "tue": 2, "tues": 2, "wednesday": 3,
    "wed": 3, "thursday": 4, "thu": 4, "thur": 4, "thurs": 4, "friday": 5, "fri": 5, "saturday": 6, "sat": 6, "sunday": 0, "sun": 0,
}
_DAY = "|".join(sorted(DAYS, key=len, reverse=True))
_HOUR = rf"\d{{1,2}}|{'|'.join(HOUR_WORDS)}"
_MARKERS = re.compile(
    r"\b(ogni|tutti|tutte|every|each|daily|weekly|monthly|hourly|feriali|feriale|weekdays?|weekends?|quotidian\w*|settimanal\w*|mensil\w*|giornalmente|fine settimana)\b"
)
_UNITS = r"(minuti|minuto|min|mins|minutes|minute|ore|ora|hours|hour|hrs|hr|h|giorni|giorno|days|day)"


class TextError(ValueError):
    """The sentence names a schedule but it cannot be used; the message says what to add or change."""


def _normalise(text: str) -> str:
    t = unicodedata.normalize("NFD", text.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.replace("’", " ").replace("'", " ")
    t = re.sub(r"[,;]", " ", t)
    t = re.sub(r"\.(?=\s|$)", "", t)
    return " ".join(t.split())


def _hour(token: str) -> int:
    return HOUR_WORDS[token] if token in HOUR_WORDS else int(token)


def _times(t: str) -> tuple[list[tuple[int, int, str]], str]:
    """Find clock times; returns ``(hour, minute, marker)`` for each, and the text with them removed."""
    found: list[tuple[int, int, str]] = []

    def take(pattern: str, build) -> None:  # type: ignore[no-untyped-def]
        nonlocal t
        for m in list(re.finditer(pattern, t)):
            found.append(build(m))
        t = re.sub(pattern, " ", t)

    take(r"\b(\d{1,2})[:.h](\d{2})\s*(am|pm)?\b", lambda m: (int(m[1]), int(m[2]), m[3] or ""))
    take(rf"\b(?:alle|ore|at|@)\s*({_HOUR})\s+e\s+(mezza|mezzo|un quarto)\b", lambda m: (_hour(m[1]), 30 if m[2] in ("mezza", "mezzo") else 15, ""))
    take(rf"\b(?:alle|ore|at|@)\s*({_HOUR})\s+e\s+(\d{{2}})\b", lambda m: (_hour(m[1]), int(m[2]), ""))
    take(r"\b(\d{1,2})\s*(am|pm)\b", lambda m: (int(m[1]), 0, m[2]))
    take(rf"\b(?:alle|ore|all|at|@)\s*({_HOUR})\s*(am|pm)?\b(?!\s*(?:{_UNITS})\b)", lambda m: (_hour(m[1]), 0, m[2] or ""))
    take(r"\b(?:mezzogiorno|noon)\b", lambda m: (12, 0, ""))
    take(r"\b(?:mezzanotte|midnight)\b", lambda m: (0, 0, ""))
    return found, t


def _adjust(times: list[tuple[int, int, str]], t: str) -> list[tuple[int, int]]:
    evening = bool(re.search(r"\b(pomeriggio|sera|stasera|evening|afternoon|pm)\b", t)) and not re.search(r"\b(mattina|mattino|morning)\b", t)
    night = bool(re.search(r"\b(notte|stanotte|night)\b", t))
    out = []
    for hour, minute, marker in times:
        if marker == "pm" and hour < 12:
            hour += 12
        elif marker == "am" and hour == 12:
            hour = 0
        elif not marker:
            if evening and hour < 12:
                hour += 12
            elif night:
                hour = 0 if hour == 12 else hour + 12 if 6 <= hour <= 11 else hour
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise TextError(f"{hour:02d}:{minute:02d} is not a valid time")
        out.append((hour, minute))
    return out


def _clock(times: list[tuple[int, int]]) -> tuple[str, str]:
    minutes = {m for _, m in times}
    if len(minutes) > 1:
        raise TextError("several times must share the same minutes (for example 8:00 and 20:00); create one task for each different time")
    return str(minutes.pop()), ",".join(str(h) for h in sorted({h for h, _ in times}))


def _weekdays(t: str) -> tuple[list[int], str]:
    days: set[int] = set()
    for m in re.finditer(rf"\b(?:dal|da|from)?\s*({_DAY})\s*(?:al|a|to|through|thru|until|-)\s*({_DAY})\b", t):
        start, end = DAYS[m[1]], DAYS[m[2]]
        cursor = start
        for _ in range(7):
            days.add(cursor)
            if cursor == end:
                break
            cursor = (cursor + 1) % 7
    t = re.sub(rf"\b(?:dal|da|from)?\s*({_DAY})\s*(?:al|a|to|through|thru|until|-)\s*({_DAY})\b", " ", t)
    if re.search(r"\b(feriali|feriale|weekdays?|working days?|giorni lavorativi)\b", t):
        days |= {1, 2, 3, 4, 5}
        t = re.sub(r"\b(nei giorni |nei |on |every |ogni )?(feriali|feriale|weekdays?|working days?|giorni lavorativi)\b", " ", t)
    if re.search(r"\b(weekends?|fine settimana|fine-settimana)\b", t):
        days |= {6, 0}
        t = re.sub(r"\b(nel |il |every |ogni |on |at the )?(weekends?|fine settimana|fine-settimana)\b", " ", t)
    for m in re.finditer(rf"\b({_DAY})\b", t):
        days.add(DAYS[m[1]])
    t = re.sub(rf"\b({_DAY})\b", " ", t)
    return sorted(days), t


def _dow_field(days: list[int]) -> str:
    ordered = sorted(days, key=lambda d: (d + 6) % 7)           # Monday first
    if ordered == [1, 2, 3, 4, 5]:
        return "1-5"
    if ordered == [6, 0]:
        return "0,6"
    return ",".join(str(d) for d in sorted(days))


def to_cron(text: str) -> str | None:
    """Turn a sentence into a cron expression or an ``every Nm|Nh|Nd`` interval.

    Returns:
        The expression, or ``None`` when the text does not look like a sentence about time (the caller reports its own error).

    Raises:
        TextError: When it is clearly a schedule sentence but is incomplete or not expressible (no time given, last day of the month, ...).
    """
    t = _normalise(text)
    if not t or not (_MARKERS.search(t) or re.search(rf"\b({_DAY})\b", t) or re.search(r"\b(mese|month)\b", t)):
        return None
    clock, rest = _times(t)
    times = _adjust(clock, t)

    # Intervals: every 30 minutes, every 2 hours, every hour
    counted = re.search(rf"\b(?:ogni|every|each)\s+(\d+)\s*{_UNITS}\b", rest)
    single = re.search(r"\b(?:ogni|every|each)\s+(ora|hour|minuto|minute)\b", rest)
    if counted or single or re.search(r"\bhourly\b", rest):
        if times:
            raise TextError("an interval such as 'every 2 hours' cannot also have a time of day; say either one or the other")
        n = int(counted[1]) if counted else 1
        unit = (counted[2] if counted else single[1] if single else "hour")[0]
        if unit == "m":
            if n % 60 == 0:
                n, unit = n // 60, "h"
            else:
                return f"*/{n} * * * *" if n < 60 and 60 % n == 0 else f"every {n}m"
        if unit in ("o", "h"):
            return "0 * * * *" if n == 1 else f"0 */{n} * * *" if n < 24 and 24 % n == 0 else f"every {n}h"
        if n == 1:
            raise TextError("at what time of day? For example 'every day at 6'")
        return f"every {n}d"

    # Monthly: on the 1st of every month, every month on the 15th
    if re.search(r"\b(mese|mesi|month|months|monthly|mensil\w*)\b", rest):
        if re.search(r"\b(ultimo giorno|last day)\b", rest):
            raise TextError("'the last day of the month' cannot be written in cron; use a day from 1 to 28")
        day = re.search(r"\b(?:il|on the|the|on|giorno)\s+(\d{1,2})(?:st|nd|rd|th|o|°)?\b", rest) or re.search(r"\b(\d{1,2})(?:st|nd|rd|th|°)\b", rest)
        number = int(day[1]) if day else 1 if re.search(r"\b(primo|first)\b", rest) else None
        if number is None:
            raise TextError("which day of the month? For example 'the 1st of every month at 8'")
        if not 1 <= number <= 31:
            raise TextError(f"day {number} is not a day of the month")
        if not times:
            raise TextError("at what time? For example 'the 1st of every month at 8'")
        minute, hours = _clock(times)
        return f"{minute} {hours} {number} * *"

    # Weekly and daily
    days, rest = _weekdays(rest)
    if days:
        if not times:
            raise TextError("at what time? For example 'every monday at 9'")
        minute, hours = _clock(times)
        return f"{minute} {hours} * * {_dow_field(days)}"
    if re.search(r"\b(settimanal\w*|weekly|ogni settimana|every week|each week)\b", rest):
        raise TextError("which day of the week? For example 'every monday at 9'")
    if re.search(r"\b(ogni giorno|tutti i giorni|tutte le|ogni mattina|ogni mattino|ogni sera|ogni notte|ogni pomeriggio|quotidian\w*|giornalmente|every day|everyday|each day|daily|every morning|every evening|every night|every afternoon)\b", rest):
        if not times:
            raise TextError("at what time? For example 'every morning at 6'")
        minute, hours = _clock(times)
        return f"{minute} {hours} * * *"
    raise TextError("I could not read this schedule. Try 'every morning at 6', 'ogni lunedì alle 9', 'every 2 hours' or a cron expression such as '0 6 * * *'")
