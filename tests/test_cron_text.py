"""Schedules written as sentences, in Italian and English (aifred.cron_text and its use by aifred.cron)."""

import pytest

from aifred import cron
from aifred.cron_text import TextError, to_cron


@pytest.mark.parametrize("text, expected", [
    # every day
    ("ogni mattina alle 6", "0 6 * * *"), ("every morning at 6", "0 6 * * *"), ("Every morning at 6am", "0 6 * * *"),
    ("ogni sera alle 8", "0 20 * * *"), ("every evening at 8", "0 20 * * *"), ("ogni notte alle 2", "0 2 * * *"),
    ("tutti i giorni alle 18:30", "30 18 * * *"), ("every day at 6:30 pm", "30 18 * * *"), ("daily at 7", "0 7 * * *"),
    ("ogni giorno alle sei", "0 6 * * *"), ("every day at six", "0 6 * * *"), ("ogni giorno alle 6 e mezza", "30 6 * * *"),
    ("ogni giorno a mezzogiorno", "0 12 * * *"), ("every day at midnight", "0 0 * * *"), ("ogni giorno alle 6.45", "45 6 * * *"),
    ("ogni giorno alle 8 e alle 20", "0 8,20 * * *"), ("every day at 8am and 6pm", "0 8,18 * * *"), ("ogni giorno alle 9:15 e 18:15", "15 9,18 * * *"),
    ("every day at 12am", "0 0 * * *"), ("every day at 12pm", "0 12 * * *"), ("ogni pomeriggio alle 5", "0 17 * * *"),
    # days of the week
    ("dal lunedì al venerdì alle 8:30", "30 8 * * 1-5"), ("from monday to friday at 8", "0 8 * * 1-5"), ("monday to friday at 8", "0 8 * * 1-5"),
    ("ogni lunedì e giovedì alle 9", "0 9 * * 1,4"), ("every monday and thursday at 9am", "0 9 * * 1,4"), ("ogni venerdì alle 17", "0 17 * * 5"),
    ("nei giorni feriali alle 7", "0 7 * * 1-5"), ("weekdays at 7", "0 7 * * 1-5"), ("nel weekend alle 10", "0 10 * * 0,6"),
    ("every weekend at 10", "0 10 * * 0,6"), ("ogni sabato alle 10 di sera", "0 22 * * 6"), ("domenica alle 11 di notte", "0 23 * * 0"),
    ("every saturday at midnight", "0 0 * * 6"), ("ogni martedì alle 9", "0 9 * * 2"), ("every sun at 8", "0 8 * * 0"),
    ("dal venerdì al lunedì alle 9", "0 9 * * 0,1,5,6"),
    # intervals
    ("ogni 2 ore", "0 */2 * * *"), ("every 6 hours", "0 */6 * * *"), ("every 5 hours", "every 5h"), ("ogni ora", "0 * * * *"),
    ("every hour", "0 * * * *"), ("hourly", "0 * * * *"), ("every 30 minutes", "*/30 * * * *"), ("ogni 15 minuti", "*/15 * * * *"),
    ("ogni 45 minuti", "every 45m"), ("ogni 120 minuti", "0 */2 * * *"), ("ogni 3 giorni", "every 3d"),
    # monthly
    ("il primo del mese alle 8", "0 8 1 * *"), ("every month on the 15th at 10", "0 10 15 * *"), ("on the 1st of every month at 8", "0 8 1 * *"),
    ("il 15 del mese alle 9", "0 9 15 * *"), ("ogni mese il 5 alle 7", "0 7 5 * *"), ("monthly on the 3rd at 6", "0 6 3 * *"),
])
def test_sentences_become_cron(text: str, expected: str) -> None:
    assert to_cron(text) == expected


@pytest.mark.parametrize("text, message", [
    ("ogni mattina", "at what time"), ("every day", "at what time"), ("every monday", "at what time"), ("ogni settimana", "which day of the week"),
    ("every week", "which day of the week"), ("every month", "which day of the month"), ("il primo del mese", "at what time"),
    ("l'ultimo giorno del mese alle 8", "last day"), ("ogni giorno alle 25", "not a valid time"), ("ogni giorno alle 8:30 e alle 20", "same minutes"),
    ("ogni 2 ore alle 8", "cannot also have a time"), ("every day at 8 every 2 hours", "cannot also have a time"), ("ogni giorno alle 6:75", "not a valid time"),
    ("every month on the 40th at 8", "not a day of the month"),
])
def test_incomplete_or_impossible_sentences_say_what_is_missing(text: str, message: str) -> None:
    with pytest.raises(TextError, match=message):
        to_cron(text)


@pytest.mark.parametrize("text", ["", "hello world", "0 6 * * *", "foo bar baz"])
def test_text_that_is_not_a_sentence_about_time_is_left_to_the_cron_parser(text: str) -> None:
    assert to_cron(text) is None


def test_parse_accepts_sentences_and_remembers_what_was_typed() -> None:
    spec = cron.parse("ogni mattina alle 6")
    assert spec.text == "0 6 * * *" and spec.source == "ogni mattina alle 6"
    assert cron.parse("0 6 * * *").source == "" and cron.parse("@daily").source == "" and cron.parse("every 30m").source == ""
    assert cron.parse("every 5 hours").interval == 5 * 3600 and cron.parse("every 5 hours").text == "every 5h"


def test_the_minimum_gap_still_applies_to_sentences() -> None:
    with pytest.raises(cron.CronError, match="more often than every 5 minutes"):
        cron.parse("every minute")
    with pytest.raises(cron.CronError, match="more often than every 5 minutes"):
        cron.parse("ogni 2 minuti")
    assert cron.parse("every minute", min_interval_minutes=1).text == "*/1 * * * *"


def test_errors_for_unreadable_text_mention_the_sentence_form() -> None:
    with pytest.raises(cron.CronError, match="every morning at 6"):
        cron.parse("foo bar baz")
    with pytest.raises(cron.CronError, match="at what time"):
        cron.parse("every morning")


def test_sentences_run_at_the_right_time() -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    rome = ZoneInfo("Europe/Rome")
    spec = cron.parse("dal lunedì al venerdì alle 8:30")
    start = datetime(2026, 10, 30, 9, 0, tzinfo=rome).timestamp()                   # a Friday after 8:30
    nxt = datetime.fromtimestamp(cron.next_run(spec, start, rome), tz=rome)
    assert (nxt.year, nxt.month, nxt.day, nxt.hour, nxt.minute) == (2026, 11, 2, 8, 30)    # the following Monday
