"""The guide (aifred/web/static/guide): every section exists in both languages, pictures and links resolve,
every setting is explained, and the schedule examples really mean what the guide says."""

from __future__ import annotations

import json
import re
import struct
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aifred import cron
from aifred.app import Services
from aifred.config import SETTING_SPECS

GUIDE = Path(__file__).resolve().parents[1] / "aifred" / "web" / "static" / "guide"
MAIN_JS = Path(__file__).resolve().parents[1] / "aifred" / "web" / "static" / "js" / "main.js"
LANGS = ("en", "it")
SECTIONS = json.loads((GUIDE / "index.json").read_text(encoding="utf-8"))["sections"]
IDS = [s["id"] for s in SECTIONS]
IMAGE = re.compile(r"!\[[^\]]*\]\(/static/guide/img/([A-Za-z0-9._-]+\.png)\)")
GUIDE_LINK = re.compile(r"\]\(#/guide\?s=([a-z]+)\)")
NOT_SETTINGS = {"AIFRED_ADMIN_USER", "AIFRED_ADMIN_PASSWORD"}


def text(lang: str, section: str) -> str:
    return (GUIDE / lang / f"{section}.md").read_text(encoding="utf-8")


def test_the_index_is_complete_and_bilingual() -> None:
    assert len(IDS) == len(set(IDS))
    for section in SECTIONS:
        assert section["title"].keys() == {"en", "it"} and section["summary"].keys() == {"en", "it"}
        assert all(section["title"][lang].strip() and section["summary"][lang].strip() for lang in LANGS)
    for lang in LANGS:
        assert {p.stem for p in (GUIDE / lang).glob("*.md")} == set(IDS), f"files in {lang}/ do not match index.json"


def test_every_page_of_the_dashboard_has_a_section() -> None:
    pages = re.findall(r'\{ id: "([a-z]+)", label:', MAIN_JS.read_text(encoding="utf-8"))
    assert pages and set(pages) <= set(IDS), f"pages without a guide section: {set(pages) - set(IDS)}"


@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("section", IDS)
def test_a_section_is_a_real_document(lang: str, section: str) -> None:
    body = text(lang, section)
    assert body.startswith("# ") and len(body) > 600, "a section starts with a title and says something"
    assert "TODO" not in body and "FIXME" not in body


def test_pictures_exist_are_used_and_are_both_languages() -> None:
    on_disk = {p.name for p in (GUIDE / "img").glob("*.png")}
    used: set[str] = set()
    for section in IDS:
        per_lang = {lang: IMAGE.findall(text(lang, section)) for lang in LANGS}
        assert per_lang["en"] == per_lang["it"], f"{section}: the two languages use different pictures"
        used.update(per_lang["en"])
    assert used <= on_disk, f"missing pictures: {used - on_disk}"
    assert on_disk <= used, f"pictures nobody uses: {on_disk - used}"


def test_pictures_are_real_png_files_of_a_sane_size() -> None:
    total = 0
    for path in (GUIDE / "img").glob("*.png"):
        data = path.read_bytes()
        assert data[:8] == b"\x89PNG\r\n\x1a\n", path.name
        width, height = struct.unpack(">II", data[16:24])
        assert (width, height) == (1280, 820), f"{path.name} is {width}x{height}"
        assert len(data) < 400_000, f"{path.name} is {len(data):,} bytes"
        total += len(data)
    assert total < 4_000_000


def test_links_between_sections_resolve() -> None:
    for lang in LANGS:
        for section in IDS:
            for target in GUIDE_LINK.findall(text(lang, section)):
                assert target in IDS, f"{lang}/{section} links to a missing section {target}"


def test_both_languages_have_the_same_structure() -> None:
    for section in IDS:
        en, it = text("en", section), text("it", section)
        assert len(re.findall(r"^#{1,3} ", en, flags=re.M)) == len(re.findall(r"^#{1,3} ", it, flags=re.M)), f"{section}: different number of headings"
        assert len(re.findall(r"^\|", en, flags=re.M)) == len(re.findall(r"^\|", it, flags=re.M)), f"{section}: different table sizes"
        assert sorted(GUIDE_LINK.findall(en)) == sorted(GUIDE_LINK.findall(it)), f"{section}: different links"


def test_every_setting_named_in_the_guide_exists() -> None:
    names = re.compile(r"`([A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+)`")
    for lang in LANGS:
        for section in IDS:
            for name in names.findall(text(lang, section)):
                assert name in SETTING_SPECS or name in NOT_SETTINGS, f"{lang}/{section}: {name} is not a setting"


@pytest.mark.parametrize("lang", LANGS)
def test_the_config_section_explains_every_setting(lang: str) -> None:
    body = text(lang, "config")
    missing = [key for key in SETTING_SPECS if f"`{key}`" not in body]
    assert not missing, f"{lang}/config.md does not explain: {missing}"


@pytest.mark.parametrize("lang", LANGS)
def test_the_schedule_examples_mean_what_the_guide_says(lang: str) -> None:
    rows = [line for line in text(lang, "schedules").splitlines() if line.startswith("| `")]
    assert len(rows) >= 15
    for row in rows:
        left, right = [cell.strip() for cell in row.strip("|").split("|")[:2]]
        stored = re.findall(r"`([^`]+)`", right)[0]
        for phrase in re.findall(r"`([^`]+)`", left):
            assert cron.parse(phrase).text == stored, f"{phrase!r} is documented as {stored!r}"


@pytest.mark.parametrize("lang", LANGS)
def test_the_commands_in_the_guide_are_the_real_ones(lang: str, services: Services) -> None:
    from aifred.commands import Commands

    table = re.search(r"## (?:The commands|I comandi)\n(.*?)\n###", text(lang, "commands"), flags=re.S)
    assert table
    documented = set(re.findall(r"`/([a-z]+)", table.group(1)))
    assert documented == set(Commands(services)._table), f"{lang}: documented {documented ^ set(Commands(services)._table)}"


def test_the_guide_is_served_with_the_dashboard(client: TestClient) -> None:
    index = client.get("/static/guide/index.json")
    assert index.status_code == 200 and index.json()["sections"][0]["id"] == "start"
    assert client.get("/static/guide/it/schedules.md").status_code == 200
    assert client.get("/static/guide/img/schedules-2-sentence.png").headers["content-type"] == "image/png"
    assert client.get("/static/guide/en/nonexistent.md").status_code == 404
