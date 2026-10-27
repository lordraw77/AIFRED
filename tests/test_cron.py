from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from aifred import cron

ROME = ZoneInfo("Europe/Rome")


def at(text: str) -> float:
    return datetime.fromisoformat(text).replace(tzinfo=ROME).timestamp()


def fmt(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=ROME).strftime("%Y-%m-%d %H:%M")


def nxt(expr: str, after: str, **kw) -> str:
    return fmt(cron.next_run(cron.parse(expr, **kw), at(after), ROME))


def test_daily_and_alias():
    assert nxt("0 8 * * *", "2026-10-27 07:59") == "2026-10-27 08:00"
    assert nxt("0 8 * * *", "2026-10-27 08:00") == "2026-10-28 08:00"  # strictly after
    assert nxt("@daily", "2026-10-27 10:00") == "2026-10-28 00:00"
    assert nxt("@hourly", "2026-10-27 10:30") == "2026-10-27 11:00"


def test_lists_ranges_steps():
    assert nxt("*/15 9-17 * * *", "2026-10-27 09:50") == "2026-10-27 10:00"
    assert nxt("*/15 9-17 * * *", "2026-10-27 17:46") == "2026-10-28 09:00"
    assert nxt("0,30 8 * * *", "2026-10-27 08:10") == "2026-10-27 08:30"
    assert nxt("0 8-18/5 * * *", "2026-10-27 09:00") == "2026-10-27 13:00"


def test_weekdays_and_names():
    # 2026-10-27 is a Tuesday
    assert nxt("0 9 * * mon", "2026-10-27 12:00") == "2026-11-02 09:00"
    assert nxt("0 9 * * 1-5", "2026-10-30 12:00") == "2026-11-02 09:00"  # Friday -> Monday
    assert nxt("0 9 * * 0", "2026-10-27 12:00") == "2026-11-01 09:00"
    assert nxt("0 9 * * 7", "2026-10-27 12:00") == "2026-11-01 09:00"  # 7 is Sunday too
    assert nxt("0 9 1 jan *", "2026-10-27 12:00") == "2027-01-01 09:00"


def test_day_of_month_or_weekday_when_both_restricted():
    # the 1st OR a Monday, like classic cron
    assert nxt("0 9 1 * mon", "2026-10-27 12:00") == "2026-11-01 09:00"
    assert nxt("0 9 1 * mon", "2026-11-01 12:00") == "2026-11-02 09:00"


def test_interval():
    spec = cron.parse("every 30m")
    assert spec.interval == 1800 and cron.next_run(spec, 1000.0) == 2800.0
    assert cron.parse("EVERY 2h").interval == 7200
    assert cron.parse("every 1d").text == "every 1d"


def test_dst_is_respected():
    # Europe/Rome leaves summer time on 2026-10-25: 08:00 stays 08:00 local
    first = cron.next_run(cron.parse("0 8 * * *"), at("2026-10-24 09:00"), ROME)
    second = cron.next_run(cron.parse("0 8 * * *"), first, ROME)
    assert fmt(first) == "2026-10-25 08:00" and fmt(second) == "2026-10-26 08:00"
    assert second - first == 24 * 3600
    assert first - at("2026-10-24 08:00") == 25 * 3600  # the clock change adds an hour in between


def test_minimum_gap_is_enforced():
    for bad in ("* * * * *", "*/2 * * * *", "every 1m", "every 4m", "0,3 * * * *"):
        with pytest.raises(cron.CronError, match="more often"):
            cron.parse(bad, min_interval_minutes=5)
    cron.parse("*/5 * * * *", min_interval_minutes=5)
    cron.parse("every 5m", min_interval_minutes=5)
    cron.parse("* * * * *", min_interval_minutes=1)


@pytest.mark.parametrize("bad", ["", "   ", "* * * *", "* * * * * *", "60 * * * *", "* 24 * * *", "* * 0 * *", "* * 32 * *",
                                 "* * * 13 *", "* * * * 8", "a * * * *", "*/0 * * * *", "5-1 * * * *", "1,,2 * * * *",
                                 "every 0m", "every m", "every 400d", "every 10s"])
def test_invalid(bad):
    with pytest.raises(cron.CronError):
        cron.parse(bad)


def test_impossible_date_is_reported():
    with pytest.raises(cron.CronError, match="never runs"):
        cron.next_run(cron.parse("0 0 31 2 *"), at("2026-01-01 00:00"), ROME)


def test_timezone():
    assert cron.timezone("") is None
    assert cron.timezone("Europe/Rome") == ROME
    with pytest.raises(cron.CronError):
        cron.timezone("Mars/Base")


def test_upcoming_and_describe():
    spec = cron.parse("0 8 * * *")
    runs = cron.upcoming(spec, 3, at("2026-10-27 09:00"), ROME)
    assert [fmt(r) for r in runs] == ["2026-10-28 08:00", "2026-10-29 08:00", "2026-10-30 08:00"]
    assert cron.describe(runs[0], ROME).startswith("2026-10-28 08:00")
