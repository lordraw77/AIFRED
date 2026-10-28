"""A demo AIFRED with believable data, for taking the screenshots of the guide (see README.md in this folder).

Run from the repository root: ``python scripts/guide-screenshots/serve.py`` (needs the dev dependencies). It starts a fake model, a fake MCP
server and a temporary data directory, seeds conversations, and serves the dashboard on http://127.0.0.1:18090 (user ``admin``, password
``demo-password``). Nothing here touches a real service.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import uvicorn  # noqa: E402
from pydantic import SecretStr  # noqa: E402

import aifred.testimage as ti  # noqa: E402

ti.random_number = lambda digits=4: "4821"

import tests.fake_mcp as fake_mcp_module  # noqa: E402
from aifred.app import create_app  # noqa: E402
from aifred.config import Settings  # noqa: E402
from tests.fake_llm import FakeLLM, Text, ToolCall  # noqa: E402
from tests.fake_mcp import FakeMcp  # noqa: E402

VMS = """ID   NAME            STATUS    CPU   MEM
100  nextcloud       running   2     4 GB
101  home-assistant  running   2     2 GB
102  plex            running   4     8 GB
103  test-lab        stopped   1     1 GB
104  backup-pbs      running   2     4 GB"""


def run_tool(name: str, args: dict) -> dict:
    if name == "list_vms":
        return {"content": [{"type": "text", "text": VMS}]}
    return {"content": [{"type": "text", "text": f"ran {name}"}]}


fake_mcp_module.run_tool = run_tool
TOOLS = [
    {"name": "list_vms", "description": "List the virtual machines of the cluster", "inputSchema": {"type": "object", "properties": {"node": {"type": "string"}}}},
    {"name": "vm_status", "description": "Status of one virtual machine", "inputSchema": {"type": "object", "properties": {"vmid": {"type": "integer"}}}},
    {"name": "restart_vm", "description": "Restart a virtual machine", "inputSchema": {"type": "object", "properties": {"vmid": {"type": "integer"}}}},
]


def call(name: str, arguments: dict, call_id: str) -> list[dict]:
    return [{"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}]


def seed(services) -> None:  # type: ignore[no-untyped-def]
    s = services.store
    s.ensure_session("web:vms", "web", "Elenca le VM del cluster Proxmox")
    s.add_message("web:vms", "user", "Elenca le VM del cluster Proxmox e dimmi quali sono spente")
    s.add_message("web:vms", "assistant", None, call("mcp__proxmox__list_vms", {}, "call_1"))
    s.add_message("web:vms", "tool", VMS, None, "call_1")
    s.add_message("web:vms", "assistant", "Il cluster ha **5 macchine virtuali**, una sola è spenta:\n\n"
                  "| ID | Nome | Stato |\n|---|---|---|\n| 100 | nextcloud | in esecuzione |\n| 101 | home-assistant | in esecuzione |\n| 102 | plex | in esecuzione |\n"
                  "| 103 | test-lab | **spenta** |\n| 104 | backup-pbs | in esecuzione |\n\n- La macchina spenta è `test-lab` (1 GB di RAM).\n- Posso riavviarla se vuoi: dimmelo.")
    s.ensure_session("web:router", "web", "Ricordami che il router è su 10.70.0.1")
    s.add_message("web:router", "user", "Ricordami che il router è su 10.70.0.1")
    s.add_message("web:router", "assistant", None, call("memory", {"action": "add", "target": "memory", "content": "The router is at 10.70.0.1"}, "call_2"))
    s.add_message("web:router", "tool", "Saved to memory.", None, "call_2")
    s.add_message("web:router", "assistant", "Fatto, me lo ricorderò: il router è su **10.70.0.1**.")
    s.ensure_session("web:roma", "web", "Chiedi a un agente di cercare le notizie della AS Roma")
    s.add_message("web:roma", "user", "Chiedi a un agente di cercare le notizie della AS Roma")
    s.add_message("web:roma", "assistant", None, call("delegate", {"agent": "researcher", "task": "Cerca le ultime notizie sulla AS Roma"}, "call_3"))
    s.add_message("web:roma", "tool", "### Roma-Cagliari\n\n- Gasperini convoca 23 giocatori; Dybala non è convocato (precauzione).\n- Oltre 60.000 spettatori attesi all'Olimpico.\n\nFonte: asroma.com", None, "call_3")
    s.add_message("web:roma", "assistant", "Ecco le ultime notizie sulla **AS Roma**:\n\n### Roma-Cagliari\n\n- Gasperini ha convocato 23 giocatori; **Dybala non è convocato** per precauzione.\n- Attesi oltre **60.000 spettatori** all'Olimpico.\n\n---\n\nFonte: [asroma.com](https://www.asroma.com)")
    s.ensure_session("tg:4242:1", "telegram", "Che ore sono a Tokyo?")
    s.add_message("tg:4242:1", "user", "Che ore sono a Tokyo?")
    s.add_message("tg:4242:1", "assistant", "A Tokyo sono le **22:15** (UTC+9).")
    s.ensure_session("cron:morning-news:1", "cron", "Scheduled: morning-news")
    s.add_message("cron:morning-news:1", "user", "Summarise the news about Python in five short bullet points.")
    s.add_message("cron:morning-news:1", "assistant", "- Python 3.14 è in beta\n- Nuovo release di FastAPI\n- PEP 779 approvato\n- Ruff 0.9 è uscito\n- Novità su uv")
    s.ensure_session("sub:researcher:1", "sub:researcher", "Cerca le ultime notizie sulla AS Roma")
    s.add_message("sub:researcher:1", "user", "Cerca le ultime notizie sulla AS Roma")
    s.add_message("sub:researcher:1", "assistant", "Roma-Cagliari: convocati 23 giocatori, 60.000 spettatori attesi.")
    services.store.session_set_parent("sub:researcher:1", "web:roma")
    s.pending_add("schedule", {"action": "create", "name": "morning-news", "expression": "0 6 * * *", "prompt": "Search the web for today's news about Python and write five bullet points.", "deliver_chat": 4242}, "web:roma")
    s.pending_add("memory", {"action": "add", "target": "user", "content": "Prefers answers in Italian"}, "web:router")
    s.journal_add("memory", "add", "Saved to memory: The NAS backup runs at 02:00", "web:router", "applied", {"target": "memory", "content": "The NAS backup runs at 02:00"})
    s.journal_add("skill", "create", "Skill backup-guide created", "web:router", "applied", {"name": "backup-guide"})
    s.journal_add("settings", "config", "Settings changed: TOOLSETS", None, "applied", {})


def main() -> None:
    fake = FakeLLM().start()
    fake.always = Text("Hello! This is the fake model of the guide's demo.", chunks=4)
    cases = [
        {"request": "Which virtual machines are stopped?", "expect": "this"}, {"request": "Restart the plex VM", "expect": "this"},
        {"request": "Show me the status of VM 100", "expect": "this"}, {"request": "Open the page https://example.org", "expect": "other"},
        {"request": "What is the capital of France?", "expect": "none"}, {"request": "Say hello", "expect": "none"},
    ]
    fake.queue(Text("4821"), ToolCall("ping", {"word": "pong"}), Text(json.dumps(cases)))   # two model tests, then the test requests of the Tools page
    mcp = FakeMcp(tools=TOOLS).start()
    print("FAKE", fake.url, flush=True)
    print("MCP", mcp.url, flush=True)
    data = Path(tempfile.mkdtemp(prefix="aifred-guide-")) / "data"
    settings = Settings(_env_file=None, admin_user="admin", admin_password=SecretStr("demo-password"), data_dir=data, api_host="127.0.0.1")
    app = create_app(settings)
    seed(app.state.services)
    uvicorn.run(app, host="127.0.0.1", port=18090, log_level="warning")


if __name__ == "__main__":
    main()
