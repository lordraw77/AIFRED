"""Tests for the Telegram gateway (aifred.gateways.telegram)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest

from aifred.app import Services
from aifred.gateways import telegram
from aifred.gateways.telegram import TelegramGateway, split_message
from tests.fake_llm import FakeLLM, Status, Text, ToolCall
from tests.fake_telegram import TOKEN, FakeTelegram

pytestmark = pytest.mark.anyio

ADMIN = 111
STRANGER = 222


@pytest.fixture
def tg() -> Any:
    server = FakeTelegram().start()
    yield server
    server.stop()


@pytest.fixture
async def bot(services: Services, tg: FakeTelegram, llm_ready: FakeLLM) -> AsyncIterator[TelegramGateway]:
    services.config.update({"TELEGRAM_TOKEN": TOKEN, "TELEGRAM_API_BASE": tg.url, "TELEGRAM_ALLOWED_IDS": [ADMIN]})
    gateway = TelegramGateway(services, poll_timeout=1)
    gateway.fake = tg  # type: ignore[attr-defined]
    gateway.llm = llm_ready  # type: ignore[attr-defined]
    yield gateway
    await gateway.stop()


async def feed(bot: TelegramGateway, text: str | None, *, user: int = ADMIN, chat: int | None = None, kind: str = "private") -> None:
    """Handle one message directly (the HTTP client is only needed for sending)."""
    import httpx

    fake: FakeTelegram = bot.fake  # type: ignore[attr-defined]
    fake.push(text, user=user, chat=chat, kind=kind)
    update = fake._updates[-1]
    bot._token, bot._base = TOKEN, fake.url
    async with httpx.AsyncClient() as client:
        bot._client = client
        await bot.handle_update(update)


async def until(condition: Callable[[], bool], seconds: float = 8.0) -> None:
    for _ in range(int(seconds / 0.02)):
        if condition():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition not reached")


# split_message


def test_split_prefers_boundaries_and_respects_the_limit() -> None:
    text = "\n\n".join(["word " * 300] * 5)
    parts = split_message(text, 1000)
    assert all(0 < len(p) <= 1000 for p in parts) and len(parts) > 1
    assert "".join(parts).replace(" ", "").replace("\n", "") == text.replace(" ", "").replace("\n", "")
    assert split_message("short") == ["short"] and split_message("   ") == ["(no answer)"]
    assert [len(p) for p in split_message("x" * 2500, 1000)] == [1000, 1000, 500]  # no boundary: hard cut


# Access control


async def test_allowed_user_gets_an_answer(bot: TelegramGateway) -> None:
    bot.llm.queue(Text("Hello Alessandro"))  # type: ignore[attr-defined]
    await feed(bot, "hi")
    assert bot.fake.texts(ADMIN) == ["Hello Alessandro"]  # type: ignore[attr-defined]
    assert bot.fake.actions[0] == (ADMIN, "typing")  # type: ignore[attr-defined]
    sessions = [r["id"] for r in bot._s.store.list_sessions()]
    assert len(sessions) == 1 and sessions[0].startswith(f"tg:{ADMIN}:")


async def test_unknown_user_is_told_their_id_and_nothing_runs(bot: TelegramGateway) -> None:
    await feed(bot, "let me in", user=STRANGER)
    [reply] = bot.fake.texts(STRANGER)  # type: ignore[attr-defined]
    assert str(STRANGER) in reply and "not authorised" in reply
    assert bot.llm.requests == [] and bot._s.store.list_sessions() == []  # type: ignore[attr-defined]
    await feed(bot, "/kb scan", user=STRANGER)
    assert bot.llm.requests == []  # type: ignore[attr-defined]


async def test_only_private_chats_are_served(bot: TelegramGateway) -> None:
    await feed(bot, "hello group", chat=-100500, kind="supergroup")
    await feed(bot, "hello channel", chat=-100600, kind="channel")
    assert bot.fake.sent == [] and bot.llm.requests == []  # type: ignore[attr-defined]


async def test_non_text_and_oversized_messages(bot: TelegramGateway) -> None:
    await feed(bot, None)
    await feed(bot, "x" * (telegram.MAX_INPUT_CHARS + 1))
    first, second = bot.fake.texts(ADMIN)  # type: ignore[attr-defined]
    assert "text messages" in first and "too long" in second and bot.llm.requests == []  # type: ignore[attr-defined]


async def test_allowlist_changes_apply_without_a_restart(bot: TelegramGateway) -> None:
    await feed(bot, "hi", user=STRANGER)
    assert "not authorised" in bot.fake.texts(STRANGER)[0]  # type: ignore[attr-defined]
    bot._s.config.update({"TELEGRAM_ALLOWED_IDS": [ADMIN, STRANGER]})
    bot.llm.queue(Text("welcome"))  # type: ignore[attr-defined]
    await feed(bot, "hi", user=STRANGER)
    assert bot.fake.texts(STRANGER)[-1] == "welcome"  # type: ignore[attr-defined]


# Open bot


async def test_open_bot_is_restricted(bot: TelegramGateway) -> None:
    bot._s.config.update({"TELEGRAM_ALLOWED_IDS": []})
    bot.llm.queue(ToolCall("memory", {"action": "add", "target": "memory", "content": "hacked"}), Text("done"))  # type: ignore[attr-defined]
    await feed(bot, "remember this", user=STRANGER)
    tools = [t["function"]["name"] for t in bot.llm.requests[0].body.get("tools", [])]  # type: ignore[attr-defined]
    assert "memory" not in tools and "knowledge_search" not in tools and "skill_save" not in tools
    assert "hacked" not in bot._s.memory.snapshot("memory").render()


async def test_open_bot_commands_are_limited(bot: TelegramGateway) -> None:
    bot._s.config.update({"TELEGRAM_ALLOWED_IDS": []})
    for command in ("/kb scan", "/approve 1", "/model fake/test-model", "/pending"):
        await feed(bot, command, user=STRANGER)
    assert all("not available here" in t for t in bot.fake.texts(STRANGER))  # type: ignore[attr-defined]
    assert bot.llm.requests == []  # type: ignore[attr-defined]
    await feed(bot, "/help", user=STRANGER)
    assert "/kb" not in bot.fake.texts(STRANGER)[-1] and "/new" in bot.fake.texts(STRANGER)[-1]  # type: ignore[attr-defined]


async def test_open_bot_rate_limit(bot: TelegramGateway) -> None:
    bot._s.config.update({"TELEGRAM_ALLOWED_IDS": []})
    now = [1000.0]
    bot._clock = lambda: now[0]
    for _ in range(telegram.OPEN_BOT_RATE):
        await feed(bot, "/help", user=STRANGER)
    await feed(bot, "/help", user=STRANGER)
    assert "too quickly" in bot.fake.texts(STRANGER)[-1]  # type: ignore[attr-defined]
    await feed(bot, "/help", user=333)  # another user has their own budget
    assert "too quickly" not in bot.fake.texts(333)[-1]  # type: ignore[attr-defined]
    now[0] += telegram.RATE_WINDOW + 1
    await feed(bot, "/help", user=STRANGER)
    assert "too quickly" not in bot.fake.texts(STRANGER)[-1]  # type: ignore[attr-defined]


async def test_allowed_users_are_not_rate_limited(bot: TelegramGateway) -> None:
    for _ in range(telegram.OPEN_BOT_RATE + 5):
        await feed(bot, "/help")
    assert all("too quickly" not in t for t in bot.fake.texts(ADMIN))  # type: ignore[attr-defined]


# Sessions, commands, replies


async def test_new_starts_a_fresh_conversation_that_persists(bot: TelegramGateway, monkeypatch: pytest.MonkeyPatch) -> None:
    bot.llm.queue(Text("one"), Text("two"), Text("three"))  # type: ignore[attr-defined]
    await feed(bot, "first")
    await feed(bot, "second")
    first = bot._s.store.kv_get(f"tg:chat:{ADMIN}")
    sent = [m["content"] for m in bot.llm.requests[1].body["messages"] if m["role"] != "system"]  # type: ignore[attr-defined]
    assert sent == ["first", "one", "second"]
    monkeypatch.setattr("aifred.commands.time.time", lambda: 9_999_999_999.0)
    await feed(bot, "/new")
    second = bot._s.store.kv_get(f"tg:chat:{ADMIN}")
    assert second != first and second.startswith(f"tg:{ADMIN}:")  # type: ignore[union-attr]
    await feed(bot, "third")
    sent = [m["content"] for m in bot.llm.requests[2].body["messages"] if m["role"] != "system"]  # type: ignore[attr-defined]
    assert sent == ["third"]


async def test_long_answers_are_split(bot: TelegramGateway) -> None:
    bot.llm.queue(Text(("paragraph " * 120 + "\n\n") * 8))  # type: ignore[attr-defined]
    await feed(bot, "write a lot")
    parts = bot.fake.texts(ADMIN)  # type: ignore[attr-defined]
    assert len(parts) >= 2 and all(len(p) <= telegram.MESSAGE_LIMIT for p in parts)


async def test_errors_are_reported_to_the_user(bot: TelegramGateway) -> None:
    bot.llm.always = Status(402)  # type: ignore[attr-defined]
    await feed(bot, "hi")
    assert bot.fake.texts(ADMIN)[0].startswith("⚠")  # type: ignore[attr-defined]


async def test_one_failing_update_does_not_stop_the_next(bot: TelegramGateway, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    async def boom(update: dict[str, Any]) -> None:
        calls.append(update)
        raise RuntimeError("bug")

    monkeypatch.setattr(bot, "handle_update", boom)
    await bot._safely({"update_id": 1})
    assert len(calls) == 1


# Polling


async def test_polling_answers_and_stores_the_offset(bot: TelegramGateway) -> None:
    tg: FakeTelegram = bot.fake  # type: ignore[attr-defined]
    bot.llm.queue(Text("polled"))  # type: ignore[attr-defined]
    tg.push("hello", user=ADMIN)
    await bot.start()
    await until(lambda: tg.texts(ADMIN) == ["polled"])
    await until(lambda: bot._s.store.kv_get(telegram.OFFSET_KEY) == "2")
    assert bot.status()["state"] == "running" and bot.status()["bot"] == "aifred_bot" and bot.status()["open"] is False
    await bot.stop()
    assert bot.status()["state"] == "stopped"
    # a new gateway resumes after the stored offset: the old update is not answered again
    again = TelegramGateway(bot._s, poll_timeout=1)
    await again.start()
    await until(lambda: tg.offsets and tg.offsets[-1] == 2)
    await asyncio.sleep(0.2)
    await again.stop()
    assert tg.texts(ADMIN) == ["polled"]


async def test_bad_token_stops_polling_and_is_never_logged(bot: TelegramGateway, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    bot._s.config.update({"TELEGRAM_TOKEN": "999:WRONG-SECRET"})
    await bot.start()
    await until(lambda: not bot.running)
    status = bot.status()
    assert status["state"] == "error" and "WRONG-SECRET" not in str(status)
    assert "WRONG-SECRET" not in caplog.text and TOKEN not in caplog.text and "bot***" in status["last_error"]


async def test_transient_errors_back_off_and_recover(bot: TelegramGateway) -> None:
    tg: FakeTelegram = bot.fake  # type: ignore[attr-defined]
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    bot._sleep = fake_sleep
    tg.failures += [(502, {"ok": False, "description": "bad gateway"}), (429, {"ok": False, "description": "slow", "parameters": {"retry_after": 7}}),
                    (409, {"ok": False, "description": "conflict"})]
    bot.llm.queue(Text("recovered"))  # type: ignore[attr-defined]
    tg.push("hello", user=ADMIN)
    await bot.start()
    await until(lambda: tg.texts(ADMIN) == ["recovered"])
    assert slept == [1.0, 7, telegram.CONFLICT_WAIT]


async def test_no_token_means_no_gateway(bot: TelegramGateway) -> None:
    bot._s.config.update({"TELEGRAM_TOKEN": ""})
    await bot.start()
    await until(lambda: not bot.running)
    assert bot.status()["state"] == "stopped" and "no token" in bot.status()["last_error"]


async def test_open_bot_warns_at_startup(bot: TelegramGateway, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)
    bot._s.config.update({"TELEGRAM_ALLOWED_IDS": []})
    await bot.start()
    await until(lambda: "open to everyone" in caplog.text)
    assert bot.status()["open"] is True


# Application wiring


def test_gateway_follows_settings_in_the_running_app(settings: Any, tg: FakeTelegram) -> None:
    from fastapi.testclient import TestClient

    from aifred.app import create_app

    with TestClient(create_app(settings), base_url="https://testserver") as client:
        gateway: TelegramGateway = client.app.state.telegram  # type: ignore[attr-defined]
        services: Services = client.app.state.services  # type: ignore[attr-defined]
        import time

        for _ in range(100):  # with no token the task ends on its own
            if not gateway.running:
                break
            time.sleep(0.05)
        assert not gateway.running
        services.config.update({"TELEGRAM_API_BASE": tg.url, "TELEGRAM_TOKEN": TOKEN})
        deadline = 100
        while not (gateway.running and gateway.state == "running") and deadline:
            time.sleep(0.05)
            deadline -= 1
        assert gateway.state == "running"
        services.config.update({"TELEGRAM_TOKEN": ""})
        deadline = 100
        while gateway.running and deadline:
            time.sleep(0.05)
            deadline -= 1
        assert not gateway.running
