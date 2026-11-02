"""Third step of the knowledge base: approximate index, collections for the open bot, folder watcher, local OCR, recordings."""

from __future__ import annotations

import json
import stat
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from aifred import rag_ann, rag_ocr, rag_watch
from aifred.rag import RagError, RagWorker
from aifred.tools.registry import ToolContext
from tests import rag_files as f
from tests.fake_llm import FakeLLM, Status, Text
from tests.rag_support import KB

pytestmark = pytest.mark.anyio

WORDS = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel", "india", "juliet", "kilo", "lima"]


def fill(kb: KB, count: int = 60, collection: str = "big") -> None:
    """``count`` one-passage documents, each about its own made-up topic."""
    kb.rag.create_collection(collection, "many")
    for i in range(count):
        kb.rag.add_document(collection, f"doc{i}.md", f"# Topic {i}\n\nThe {WORDS[i % 12]} procedure number zq{i} restarts service {WORDS[(i * 5) % 12]}x{i}.".encode())
    kb.index()


# approximate index


def test_kmeans_groups_and_assigns() -> None:
    import numpy as np

    rng = np.random.default_rng(1)
    a = rng.normal([5, 0, 0], 0.1, (40, 3))
    b = rng.normal([0, 5, 0], 0.1, (40, 3))
    centres = rag_ann.kmeans(np.vstack([a, b]).astype("float32"), 2)
    labels = rag_ann.assign(np.vstack([a, b]).astype("float32"), centres)
    assert len(set(labels[:40])) == 1 and len(set(labels[40:])) == 1 and labels[0] != labels[-1]
    assert rag_ann.nearest_cells(np.array([1, 0, 0], dtype="float32"), centres, 1) == [int(labels[0])]
    assert rag_ann.cell_count(10) == 16 and rag_ann.cell_count(1_000_000) == 1000 and rag_ann.cell_count(10**9) == 4096


def test_no_index_below_the_size_where_it_pays_off(kb: KB) -> None:
    fill(kb)
    assert kb.rag.build_index("big") == {"built": False, "cells": 0, "members": 60, "reason": "below the size where an index pays off"}
    assert kb.rag.maintain_indexes() == 0 and kb.services.store.rag_ann_get(1, "emb|embed-1") is None


def test_an_index_gives_the_same_answers_when_every_cell_is_read(kb: KB) -> None:
    fill(kb)
    exact = {h.source for h in kb.rag.search("zq17 restarts", mode="dense").hits}
    kb.services.config.update({"RAG_ANN": "on", "RAG_ANN_PROBE": 256})
    built = kb.rag.build_index("big", force=True)
    assert built["built"] and built["members"] == 60 and built["cells"] == 16
    approx = {h.source for h in kb.rag.search("zq17 restarts", mode="dense").hits}
    assert approx == exact and "doc17.md" in approx
    [status] = kb.rag.index_status()
    assert status["indexed"] and status["used"] and status["unindexed"] == 0


def test_the_index_is_really_used_and_probes_few_cells(kb: KB, monkeypatch: pytest.MonkeyPatch) -> None:
    fill(kb)
    kb.services.config.update({"RAG_ANN": "on", "RAG_ANN_PROBE": 1})
    kb.rag.build_index("big", force=True)
    calls: list[list[int] | None] = []
    store = kb.services.store
    original = store.rag_ann_vectors
    monkeypatch.setattr(store, "rag_ann_vectors", lambda *a: calls.append(a[2]) or original(*a))
    monkeypatch.setattr(store, "rag_vector_blocks", lambda *a, **k: pytest.fail("the exact scan ran"))
    hits = kb.rag.search("zq17 restarts", mode="dense").hits
    assert hits and calls[0] is not None and len(calls[0]) == 1 and calls[1] is None


def test_documents_added_or_removed_after_the_build_are_still_right(kb: KB) -> None:
    fill(kb)
    kb.services.config.update({"RAG_ANN": "on", "RAG_ANN_PROBE": 1})
    kb.rag.build_index("big", force=True)
    kb.rag.add_document("big", "late.md", b"# Late\n\nThe quokka procedure qw99 restarts the espresso machine.")
    kb.index()
    assert kb.rag.search("quokka espresso", mode="dense").hits[0].source == "late.md"   # found through the not-yet-indexed remainder
    assert kb.rag.index_status()[0]["unindexed"] == 1
    kb.rag.delete_document(kb.doc("doc17.md")["id"])
    assert "doc17.md" not in [h.source for h in kb.rag.search("zq17 restarts", mode="dense").hits]


def test_auto_builds_at_the_threshold_rebuilds_when_behind_and_drops_when_small(kb: KB) -> None:
    fill(kb, 150)
    cfg = kb.services.config
    cfg.update({"RAG_ANN_MIN_CHUNKS": 200})
    assert kb.rag.maintain_indexes() == 0                                    # 150 < 200
    cfg.update({"RAG_ANN_MIN_CHUNKS": 100})
    assert kb.rag.maintain_indexes() == 1 and kb.rag.maintain_indexes() == 0   # built, then nothing to do
    for i in range(160):
        kb.rag.add_document("big", f"more{i}.md", f"# More {i}\n\nExtra text number {i} about {WORDS[i % 12]}.".encode())
    kb.index()
    assert kb.rag.maintain_indexes() == 1                                    # more than a tenth is outside it: rebuilt
    cfg.update({"RAG_ANN_MIN_CHUNKS": 100_000})
    kb.rag.maintain_indexes()
    assert kb.services.store.rag_ann_get(1, "emb|embed-1") is None


def test_the_index_can_be_turned_off_and_goes_with_its_collection(kb: KB) -> None:
    fill(kb)
    kb.services.config.update({"RAG_ANN": "on"})
    kb.rag.build_index("big", force=True)
    kb.services.config.update({"RAG_ANN": "off"})
    kb.rag.maintain_indexes()
    assert kb.services.store.rag_ann_get(1, "emb|embed-1") is None
    kb.services.config.update({"RAG_ANN": "on"})
    kb.rag.build_index("big", force=True)
    kb.rag.delete_collection("big")
    assert kb.services.store._query("SELECT COUNT(*) AS n FROM rag_ann_cells")[0]["n"] == 0
    with pytest.raises(RagError):
        kb.rag.build_index("big")


# collections for the open bot


def tools(kb: KB, restricted: bool) -> set[str]:
    ctx = ToolContext(kb.services, "tg:5:1" if restricted else "cli:1", "telegram", restricted)
    return {t.name for t in kb.services.tools.for_context(ctx)}


async def run(kb: KB, name: str, args: dict[str, Any]) -> str:
    return await kb.services.tools.execute(ToolContext(kb.services, "tg:5:1", "telegram", True), name, json.dumps(args))


@pytest.fixture
def opened(kb: KB) -> KB:
    kb.services.config.update({"TOOLSETS": "memory,search,skills,web,file,knowledge", "TOOLSETS_TELEGRAM": ["search", "skills", "web", "knowledge"]})
    kb.add("faq.md", "# FAQ\n\nThe guest wifi password rotates every Monday morning.", "public")
    kb.add("secret.md", "# Secret\n\nThe NAS admin recovery phrase is kept in the safe.", "private")
    kb.index()
    return kb


def test_the_open_bot_sees_nothing_until_a_collection_is_opened(opened: KB) -> None:
    assert not {"knowledge_search", "knowledge_sources"} & tools(opened, restricted=True)
    opened.rag.update_collection("public", {"open_access": True})
    assert {"knowledge_search", "knowledge_sources"} <= tools(opened, restricted=True)
    assert opened.rag.collection("public")["open_access"] is True and opened.rag.collection("private")["open_access"] is False


def test_the_toolset_still_has_to_be_on_for_the_gateway(opened: KB) -> None:
    opened.rag.update_collection("public", {"open_access": True})
    opened.services.config.update({"TOOLSETS_TELEGRAM": ["search", "skills", "web"]})
    assert not {"knowledge_search", "knowledge_sources"} & tools(opened, restricted=True)


async def test_the_open_bot_reaches_only_open_collections(opened: KB) -> None:
    opened.rag.update_collection("public", {"open_access": True})
    found = await run(opened, "knowledge_search", {"query": "wifi password Monday"})
    assert "faq.md" in found
    assert "recovery phrase" not in await run(opened, "knowledge_search", {"query": "NAS admin recovery phrase safe"})
    assert await run(opened, "knowledge_search", {"query": "recovery phrase", "collection": "private"}) == "Error: unknown collection 'private'"
    listing = await run(opened, "knowledge_sources", {})
    assert "public" in listing and "private" not in listing


async def test_the_owner_still_sees_everything(opened: KB) -> None:
    opened.rag.update_collection("public", {"open_access": True})
    out = await opened.services.tools.execute(ToolContext(opened.services, "cli:1", "cli"), "knowledge_search", json.dumps({"query": "recovery phrase safe"}))
    assert "secret.md" in out


def test_the_prompt_lists_only_what_the_caller_may_search(opened: KB) -> None:
    opened.rag.update_collection("public", {"open_access": True})
    prompt = opened.services.agent.build_prompt(ToolContext(opened.services, "tg:5:1", "telegram", True)).text
    assert "public" in prompt and "private" not in prompt


def test_opening_a_collection_is_journaled_and_over_the_api(opened: KB) -> None:
    opened.rag.update_collection("public", {"open_access": True})
    assert any("opened to the open bot" in j["summary"] for j in opened.services.store.journal_list(limit=10))


# folder watcher


def wait_for(condition: Callable[[], Any], seconds: float = 8.0) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        if condition():
            return True
        time.sleep(0.05)
    return False


needs_inotify = pytest.mark.skipif(not rag_watch.available(), reason="inotify needs Linux")


@needs_inotify
def test_the_watcher_reports_one_scan_per_burst_and_per_collection(tmp_path: Path) -> None:
    folder = tmp_path / "knowledge"
    (folder / "homelab").mkdir(parents=True)
    seen: list[str | None] = []
    watcher = rag_watch.FolderWatcher(folder, seen.append, settle=0.3)
    assert watcher.start() and watcher.watches == 2
    try:
        for i in range(5):
            (folder / "homelab" / f"n{i}.md").write_text("x")
        (folder / ".hidden").write_text("x")
        assert wait_for(lambda: seen == ["homelab"])
        time.sleep(0.6)
        assert seen == ["homelab"]                                                # a burst, one scan; hidden names are ignored
        (folder / "recipes").mkdir()                                              # a new collection folder is scanned (and so created)
        assert wait_for(lambda: "recipes" in seen)
        seen.clear()
        (folder / "recipes" / "pasta.md").write_text("x")                         # and the new folder is watched too
        assert wait_for(lambda: "recipes" in seen)
        (folder / "homelab" / "n0.md").unlink()
        seen.clear()
        assert wait_for(lambda: "homelab" in seen)
    finally:
        watcher.stop()


@needs_inotify
def test_removing_a_collection_folder_scans_everything(tmp_path: Path) -> None:
    import shutil

    folder = tmp_path / "knowledge"
    (folder / "gone").mkdir(parents=True)
    seen: list[str | None] = []
    watcher = rag_watch.FolderWatcher(folder, seen.append, settle=0.3)
    watcher.start()
    try:
        shutil.rmtree(folder / "gone")
        assert wait_for(lambda: None in seen)
    finally:
        watcher.stop()


@needs_inotify
def test_the_worker_indexes_a_file_dropped_in_the_folder(kb: KB) -> None:
    kb.services.config.update({"RAG_WATCH": True, "RAG_WATCH_SETTLE": 0.5})
    worker = RagWorker(kb.rag, poll=0.1)
    worker.start()
    try:
        assert worker.watching
        (kb.rag.folder / "homelab").mkdir(parents=True, exist_ok=True)
        (kb.rag.folder / "homelab" / "ups.md").write_text("# UPS\n\nThe UPS beeps twice when the battery is low.")
        assert wait_for(lambda: any(d["source"] == "ups.md" and d["status"] == "ready" for d in kb.rag.documents()), 25)
        (kb.rag.folder / "homelab" / "ups.md").unlink()
        assert wait_for(lambda: not kb.rag.documents(), 25)
        kb.services.config.update({"RAG_WATCH": False})
        assert wait_for(lambda: not worker.watching, 25)
    finally:
        worker.stop()


def test_the_watcher_is_off_by_default_and_the_status_says_so(kb: KB) -> None:
    worker = RagWorker(kb.rag, poll=0.1)
    worker.start()
    try:
        assert not worker.watching and kb.rag.status()["watch"] is False
    finally:
        worker.stop()


# local OCR


@pytest.fixture
def tesseract(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A stand-in for the tesseract binary: it reads the image from stdin and prints a fixed text, logging its arguments."""
    script = tmp_path / "bin" / "tesseract"
    script.parent.mkdir()
    log = tmp_path / "args.log"
    script.write_text(f'#!/bin/sh\necho "$@" >> {log}\nhead -c 1 >/dev/null\ncat "{tmp_path}/ocr.txt"\n')
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    (tmp_path / "ocr.txt").write_text("Router reset: hold the recovery button for twelve seconds.\n\n\n\nThen wait for the green light.\n")
    monkeypatch.setattr(rag_ocr, "BINARY", str(script))
    return log


def ingest_image(kb: KB, name: str, data: bytes) -> dict[str, Any]:
    kb.rag.create_collection("lab", "t") if not kb.services.store.rag_collection_get("lab") else None
    kb.rag.add_document("lab", name, data)
    kb.index()
    return kb.doc(name)


def test_tesseract_reads_an_image_without_any_vision_model(kb: KB, tesseract: Path) -> None:
    kb.services.config.update({"RAG_OCR_ENGINE": "tesseract"})
    doc = ingest_image(kb, "label.png", f.make_png())
    assert doc["status"] == "ready"
    hit = kb.rag.search("recovery button twelve seconds", mode="keyword").hits[0]
    assert hit.source == "label.png" and hit.vision and "green light" in hit.text and "\n\n\n" not in hit.text
    args = tesseract.read_text().split()
    assert args[:4] == ["stdin", "stdout", "-l", "eng+ita"]
    assert kb.fake.requests == []


def test_tesseract_alone_fails_clearly_when_it_is_missing(kb: KB, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rag_ocr, "BINARY", "no-such-ocr-engine")
    kb.services.config.update({"RAG_OCR_ENGINE": "tesseract"})
    doc = ingest_image(kb, "label.png", f.make_png())
    assert doc["status"] == "error" and "tesseract is not installed" in doc["error"]


def test_auto_falls_back_to_the_vision_model_when_tesseract_is_missing_or_finds_nothing(kb: KB, fake_llm: Callable[[], FakeLLM], monkeypatch: pytest.MonkeyPatch) -> None:
    fake = fake_llm()
    p = kb.services.providers.create({"slug": "vis", "type": "local", "base_url": fake.url})
    kb.services.providers.save_models(p.id, [{"model_id": "v"}])
    kb.services.store.model_set_vision_test(p.id, "v", True)
    kb.services.llm.set_route("vision", [{"provider": "vis", "model": "v"}])
    fake.always = Text("TEXT: \nDESCRIPTION: a cat on a sofa.\nKEYWORDS: cat, sofa")
    kb.services.config.update({"RAG_OCR_ENGINE": "auto"})
    monkeypatch.setattr(rag_ocr, "BINARY", "no-such-ocr-engine")
    assert ingest_image(kb, "cat.png", f.make_png("1111"))["status"] == "ready" and len(fake.requests) == 1
    # installed but the picture has no text: the vision model describes it
    script = Path(kb.services.settings.data_dir) / "empty-ocr"
    script.write_text("#!/bin/sh\nhead -c 1 >/dev/null\n")
    script.chmod(0o755)
    monkeypatch.setattr(rag_ocr, "BINARY", str(script))
    assert ingest_image(kb, "cat2.png", f.make_png("2222"))["status"] == "ready" and len(fake.requests) == 2


def test_auto_keeps_the_tesseract_text_when_it_is_long_enough(kb: KB, tesseract: Path) -> None:
    kb.services.config.update({"RAG_OCR_ENGINE": "auto"})
    assert ingest_image(kb, "label.png", f.make_png())["status"] == "ready" and kb.fake.requests == []


def test_a_bad_language_string_is_refused_before_anything_runs(tesseract: Path) -> None:
    with pytest.raises(rag_ocr.OcrError, match="invalid RAG_OCR_LANGS"):
        rag_ocr.read(b"x", "eng; rm -rf /", timeout=5, memory_mb=1536)
    assert not tesseract.exists()


def test_the_engine_has_a_time_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    slow = tmp_path / "slow-ocr"
    slow.write_text("#!/bin/sh\nsleep 30\n")
    slow.chmod(0o755)
    monkeypatch.setattr(rag_ocr, "BINARY", str(slow))
    started = time.time()
    with pytest.raises(rag_ocr.OcrError, match="took longer"):
        rag_ocr.read(b"x", "eng", timeout=1, memory_mb=1536)
    assert time.time() - started < 10


# recordings

WAV = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 40


@pytest.fixture
def audio(kb: KB) -> KB:
    kb.services.providers.create({"slug": "stt", "type": "local", "base_url": kb.fake.url})
    kb.services.config.update({"RAG_FORMATS": ["text", "audio"], "RAG_AUDIO_PROVIDER": "stt", "RAG_AUDIO_MODEL": "whisper-1", "RAG_AUDIO_LANGUAGE": "en"})
    return kb


def test_a_recording_is_transcribed_and_found_with_its_time(audio: KB) -> None:
    audio.fake.transcript = [
        {"start": 0.0, "end": 5.0, "text": "To restart the proxmox web proxy run systemctl restart pveproxy."},
        {"start": 65.0, "end": 70.0, "text": "Afterwards check the cluster with pvecm status."},
    ]
    audio.rag.create_collection("talks", "meetings")
    audio.rag.add_document("talks", "standup.wav", WAV)
    audio.index()
    doc = audio.doc("standup.wav")
    assert doc["status"] == "ready" and doc["kind"] == "audio"
    hit = audio.rag.search("pvecm status cluster", mode="keyword").hits[0]
    assert hit.source == "standup.wav" and hit.vision and "pveproxy" in hit.text and hit.heading == "0:00"
    [request] = audio.fake.transcribe_requests
    assert b"whisper-1" in request.body["raw"] and b'name="language"' in request.body["raw"] and b"filename=" in request.body["raw"]
    assert request.body["content_type"].startswith("multipart/form-data")


def test_the_transcript_is_cached_by_the_hash_of_the_file(audio: KB) -> None:
    audio.rag.create_collection("talks", "m")
    audio.rag.add_document("talks", "a.wav", WAV)
    audio.index()
    audio.rag.add_document("talks", "copy.wav", WAV)
    audio.rag.reindex()
    audio.index()
    assert len(audio.fake.transcribe_requests) == 1 and audio.doc("copy.wav")["status"] == "ready"


def test_a_server_without_verbose_json_still_works(audio: KB) -> None:
    audio.fake.transcribe_verbose = False
    audio.rag.create_collection("talks", "m")
    audio.rag.add_document("talks", "a.wav", WAV)
    audio.index()
    assert audio.doc("a.wav")["status"] == "ready" and len(audio.fake.transcribe_requests) == 2
    assert audio.rag.search("restart proxmox", mode="keyword").hits


def test_recordings_are_refused_until_they_are_turned_on_and_configured(kb: KB) -> None:
    kb.rag.create_collection("talks", "m")
    with pytest.raises(RagError, match="audio files are turned off"):
        kb.rag.add_document("talks", "a.wav", WAV)
    kb.services.config.update({"RAG_FORMATS": ["text", "audio"]})
    kb.rag.add_document("talks", "a.wav", WAV)
    kb.index()
    assert "RAG_AUDIO_PROVIDER" in kb.doc("a.wav")["error"] and kb.fake.transcribe_requests == []


def test_a_busy_transcription_service_makes_the_document_wait(audio: KB) -> None:
    audio.fake.transcribe_steps = [Status(503)] * 4
    audio.rag.create_collection("talks", "m")
    audio.rag.add_document("talks", "a.wav", WAV)
    audio.rag.embedder._sleep = lambda s: None
    audio.index()
    assert audio.doc("a.wav")["status"] == "waiting" and "transcription service" in audio.doc("a.wav")["error"]


def test_a_permanent_transcription_error_is_a_failure(audio: KB) -> None:
    audio.fake.transcribe_steps = [Status(400, body={"error": {"message": "bad file"}}), Status(400, body={"error": {"message": "bad file"}})]
    audio.rag.create_collection("talks", "m")
    audio.rag.add_document("talks", "a.wav", WAV)
    audio.index()
    doc = audio.doc("a.wav")
    assert doc["status"] == "error" and "transcription failed" in doc["error"]


def test_the_kind_of_sound_file_comes_from_its_bytes(audio: KB) -> None:
    for name, head in [("a.mp3", b"ID3\x03\x00\x00"), ("b.ogg", b"OggS\x00\x02"), ("c.flac", b"fLaC\x00\x00"), ("d.m4a", b"\x00\x00\x00\x20ftypM4A "), ("e.bin", WAV)]:
        assert audio.rag.accepts(name, head + b"\x00" * 16) == "audio"
    with pytest.raises(RagError, match="not a valid .mp3"):
        audio.rag.accepts("fake.mp3", b"just some text, not sound")


def test_a_recording_over_its_limit_is_refused(audio: KB) -> None:
    audio.services.config.update({"RAG_MAX_AUDIO_BYTES": 1024})
    audio.rag.create_collection("talks", "m")
    with pytest.raises(RagError, match=r"recording is .*RAG_MAX_AUDIO_BYTES"):
        audio.rag.add_document("talks", "a.wav", WAV + b"\x00" * 2000)
