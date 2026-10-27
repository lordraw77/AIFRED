"""Schedule expressions for the scheduler: standard cron, aliases and plain intervals.

Accepted forms (all evaluated in one time zone, the server's or ``SCHEDULER_TIMEZONE``):

* ``m h dom mon dow``: five cron fields with ``*``, lists (``1,15``), ranges (``9-17``), steps (``*/10``, ``8-18/2``),
  month names (``jan``) and day names (``mon``; Sunday is 0 or 7). When both the day of the month and the day of the
  week are restricted a day matches if *either* does, as in classic cron.
* ``@hourly``, ``@daily`` (``@midnight``), ``@weekly``, ``@monthly``, ``@yearly`` (``@annually``).
* ``every 30m``, ``every 2h``, ``every 1d``: a fixed interval, counted from when the schedule was created or last ran.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ALIASES = {
    "@hourly": "0 * * * *", "@daily": "0 0 * * *", "@midnight": "0 0 * * *", "@weekly": "0 0 * * 0",
    "@monthly": "0 0 1 * *", "@yearly": "0 0 1 1 *", "@annually": "0 0 1 1 *",
}
MONTHS = {n: i for i, n in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split(), start=1)}
DAYS = {n: i for i, n in enumerate("sun mon tue wed thu fri sat".split())}
_INTERVAL = re.compile(r"^every\s+(\d{1,4})\s*([mhd])$", re.IGNORECASE)
_UNIT_SECONDS = {"m": 60, "h": 3600, "d": 86400}
SEARCH_YEARS = 6


class CronError(ValueError):
    """The expression is not valid; the message says what is wrong."""


@dataclass(frozen=True)
class Spec:
    """A parsed schedule.

    Attributes:
        text: The expression as normalised for display.
        interval: Seconds between runs for ``every …`` schedules, else ``None``.
        minutes: Allowed minutes (cron form).
        hours: Allowed hours.
        days: Allowed days of the month.
        months: Allowed months.
        weekdays: Allowed days of the week, 0 = Sunday.
        dom_any: Whether the day-of-month field is ``*``.
        dow_any: Whether the day-of-week field is ``*``.
    """

    text: str
    interval: int | None = None
    minutes: frozenset[int] = frozenset()
    hours: frozenset[int] = frozenset()
    days: frozenset[int] = frozenset()
    months: frozenset[int] = frozenset()
    weekdays: frozenset[int] = frozenset()
    dom_any: bool = True
    dow_any: bool = True


def _field(text: str, low: int, high: int, names: dict[str, int] | None, what: str) -> frozenset[int]:
    values: set[int] = set()
    for part in text.split(","):
        if not part:
            raise CronError(f"empty item in the {what} field")
        step = 1
        if "/" in part:
            part, _, step_text = part.partition("/")
            if not step_text.isdigit() or int(step_text) < 1:
                raise CronError(f"bad step in the {what} field: {step_text!r}")
            step = int(step_text)
        if part == "*":
            start, end = low, high
        elif "-" in part:
            a, _, b = part.partition("-")
            start, end = _number(a, names, what), _number(b, names, what)
        else:
            start = _number(part, names, what)
            end = high if step > 1 else start
        if not (low <= start <= high and low <= end <= high and start <= end):
            raise CronError(f"{part!r} is outside {low}-{high} in the {what} field")
        values.update(range(start, end + 1, step))
    if what == "day of the week" and 7 in values:
        values.discard(7)
        values.add(0)
    return frozenset(values)


def _number(text: str, names: dict[str, int] | None, what: str) -> int:
    if names and text.lower() in names:
        return names[text.lower()]
    if not text.isdigit():
        raise CronError(f"{text!r} is not a valid value in the {what} field")
    return int(text)


def parse(expression: str, min_interval_minutes: int = 5) -> Spec:
    """Parse a schedule expression.

    Args:
        expression: Cron fields, an alias or ``every Nm|Nh|Nd``.
        min_interval_minutes: Shortest allowed gap between runs (protects budgets and the model).

    Raises:
        CronError: If the expression is invalid or runs more often than allowed.
    """
    text = " ".join(expression.strip().split())
    if not text:
        raise CronError("the schedule is empty")
    if (m := _INTERVAL.match(text)):
        seconds = int(m.group(1)) * _UNIT_SECONDS[m.group(2).lower()]
        if seconds < min_interval_minutes * 60:
            raise CronError(f"a schedule may not run more often than every {min_interval_minutes} minutes")
        if seconds > 366 * 86400:
            raise CronError("an interval cannot be longer than a year")
        return Spec(f"every {m.group(1)}{m.group(2).lower()}", interval=seconds)
    text = ALIASES.get(text.lower(), text)
    fields = text.split(" ")
    if len(fields) != 5:
        raise CronError("use five fields (minute hour day month weekday), an alias such as @daily, or 'every 30m'")
    minutes = _field(fields[0], 0, 59, None, "minute")
    hours = _field(fields[1], 0, 23, None, "hour")
    days = _field(fields[2], 1, 31, None, "day of the month")
    months = _field(fields[3], 1, 12, MONTHS, "month")
    weekdays = _field(fields[4], 0, 7, DAYS, "day of the week")
    spec = Spec(" ".join(fields), None, minutes, hours, days, months, weekdays, fields[2].startswith("*"), fields[4].startswith("*"))
    times = sorted(h * 60 + m for h in hours for m in minutes)
    gaps = [b - a for a, b in zip(times, times[1:], strict=False)]
    if gaps and min(gaps) < min_interval_minutes:
        raise CronError(f"a schedule may not run more often than every {min_interval_minutes} minutes")
    return spec


def timezone(name: str = "") -> ZoneInfo | None:
    """The zone named by ``SCHEDULER_TIMEZONE`` (empty: the server's local zone, ``None``).

    Raises:
        CronError: For an unknown zone.
    """
    if not name:
        return None
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise CronError(f"unknown time zone {name!r} (for example Europe/Rome)") from None


def next_run(spec: Spec, after: float, zone: ZoneInfo | None = None) -> float:
    """The first run strictly after ``after`` (epoch seconds), or for intervals ``after`` plus the interval.

    Raises:
        CronError: If nothing matches within six years (for example ``0 0 31 2 *``).
    """
    if spec.interval is not None:
        return after + spec.interval
    start = datetime.fromtimestamp(after, tz=zone) if zone else datetime.fromtimestamp(after).astimezone()
    tz = start.tzinfo
    day = start.replace(hour=0, minute=0, second=0, microsecond=0)
    for _ in range(366 * SEARCH_YEARS):
        if day.month in spec.months and _day_matches(spec, day):
            for hour in sorted(spec.hours):
                for minute in sorted(spec.minutes):
                    candidate = _at(day, hour, minute, tz)
                    if candidate.timestamp() > after:
                        return candidate.timestamp()
        day = _at(day, 0, 0, tz) + timedelta(days=1)
        day = _at(day, 0, 0, tz)
    raise CronError("this schedule never runs (check the day and month)")


def _at(day: datetime, hour: int, minute: int, tz: object) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz)  # type: ignore[arg-type]


def _day_matches(spec: Spec, day: datetime) -> bool:
    dom = day.day in spec.days
    dow = (day.weekday() + 1) % 7 in spec.weekdays  # Python: Monday is 0; cron: Sunday is 0
    if spec.dom_any and spec.dow_any:
        return True
    if spec.dom_any:
        return dow
    if spec.dow_any:
        return dom
    return dom or dow


def upcoming(spec: Spec, count: int, after: float, zone: ZoneInfo | None = None) -> list[float]:
    """The next ``count`` run times."""
    out, t = [], after
    for _ in range(count):
        t = next_run(spec, t, zone)
        out.append(t)
    return out


def describe(timestamp: float, zone: ZoneInfo | None = None) -> str:
    """A run time as ``2026-10-28 08:00 CET`` in the schedule's zone."""
    moment = datetime.fromtimestamp(timestamp, tz=zone) if zone else datetime.fromtimestamp(timestamp).astimezone()
    return moment.strftime("%Y-%m-%d %H:%M %Z").strip()
