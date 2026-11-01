"""A small evaluation of long-term memory end to end: conversations are read, facts stored, later questions must find them.

It measures what the unit tests do not: that the whole path (reading, storing, searching, ranking) brings back the
right fact for a question phrased differently, and brings back nothing for a question unrelated to anything known.
The extraction model is scripted, so this checks the machinery, not a real model's judgement.
"""

from __future__ import annotations

import json

import pytest

from aifred.app import Services
from tests.fake_llm import FakeLLM, Text
from tests.rag_support import KB

pytestmark = pytest.mark.anyio

# (what the user said, the fact the model keeps)
CONVERSATIONS = [
    ("Our backup of the cluster runs every night at 02:00", "Cluster backups run every night at 02:00"),
    ("The storage pool is called tank and holds the media", "The disk pool tank holds the media files"),
    ("I want every upgrade tested on node pve3 first", "Every update is tested on node pve3 first"),
    ("The office printer is a Brother laser on VLAN 20", "The office printer is a Brother laser on VLAN 20"),
    ("My dentist appointment is on 2026-12-03", "The user has a dentist appointment on 2026-12-03"),
    ("The wiki runs on the container called docs", "The wiki runs in the container docs"),
    ("Marco leads the Atlas project and reports to Anna", "Marco leads the Atlas project and reports to Anna"),
    ("The VPN endpoint is vpn.example.org on port 51820", "The VPN endpoint is vpn.example.org port 51820"),
]

# (question, index of the fact that must come back)
QUESTIONS = [
    ("when does the snapshot of the cluster happen", 0),
    ("where are the media stored", 1),
    ("which node gets the aggiornamento first", 2),
    ("what printer do we have in the office", 3),
    ("when is my dentist", 4),
    ("what container hosts the wiki", 5),
    ("who leads Atlas", 6),
    ("what is the VPN port", 7),
]
UNRELATED = ["what is the capital of Australia", "recommend a good pasta recipe", "how tall is Mount Everest"]


async def learn(services: Services, llm: FakeLLM) -> None:
    for i, (said, kept) in enumerate(CONVERSATIONS):
        session = f"cli:eval{i}"
        services.store.ensure_session(session, "cli", "t")
        services.store.add_message(session, "user", said)
        services.store.add_message(session, "assistant", "Noted.")
        services.ltm.after_turn(session, "cli", False)
        llm.queue(Text(json.dumps({"facts": [{"text": kept, "kind": "fact", "importance": 0.6, "entities": [], "scope": "chat", "replaces": None}], "summary": ""})))
        assert (await services.ltm.extractor.run(session))["facts"] == 1


async def hit_rate(services: Services) -> tuple[float, float]:
    found = noise = 0
    for question, index in QUESTIONS:
        block = await services.ltm.recall_block("cli:ask", "cli", False, question)
        found += CONVERSATIONS[index][1] in block
    for question in UNRELATED:
        noise += bool(await services.ltm.recall_block("cli:ask", "cli", False, question))
    return found / len(QUESTIONS), noise / len(UNRELATED)


async def test_keywords_alone_find_most_facts_and_stay_quiet_on_unrelated_questions(services: Services, llm_ready: FakeLLM) -> None:
    services.config.update({"AUTO_MEMORY": "owner"})
    await learn(services, llm_ready)
    recall, noise = await hit_rate(services)
    print(f"keywords only: recall {recall:.2f}, noise {noise:.2f}")
    assert recall >= 0.6 and noise == 0.0


async def test_vectors_find_facts_worded_differently(services: Services, llm_ready: FakeLLM, kb: KB) -> None:
    services.config.update({"AUTO_MEMORY": "owner"})
    await learn(services, llm_ready)
    assert services.store._one("SELECT COUNT(*) AS n FROM mem_facts WHERE embedding IS NOT NULL")["n"] == len(CONVERSATIONS)  # made while reading
    assert services.ltm.reembed() == 0
    recall, noise = await hit_rate(services)
    print(f"with vectors: recall {recall:.2f}, noise {noise:.2f}")
    assert recall >= 0.85 and noise <= 0.35
