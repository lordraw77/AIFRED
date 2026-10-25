<p align="center">
  <img src="brand/lockup.svg" alt="AIFRED" height="72">
</p>

<p align="center"><strong>A lean, self-hosted personal AI agent. Python, Docker, your models, your data.</strong></p>


## What is AIFRED?

AIFRED is a small, from-scratch personal agent. It keeps the parts that matter (persistent memory, skills that improve over time, tools, several chat gateways) and drops the weight: one Python process, one SQLite database, one Docker container.

It runs on your hardware, talks to the LLMs you choose (local or hosted), and is configured entirely from a web dashboard.

## Highlights

- **One agent core, four gateways:** CLI, Telegram, an OpenAI-compatible API (`/v1/chat/completions`, stateful sessions) and a web dashboard.
- **Bring any LLM, configure it from the web:** Ollama and local OpenAI-compatible servers (vLLM, llama.cpp, LM Studio), plus Groq, OpenRouter, Google Gemini, Cloudflare Workers AI, Cerebras, Mistral, NVIDIA and Hugging Face. Any other OpenAI-compatible endpoint works as a custom provider.
- **Routes and automatic fallback:** ordered provider/model lists per gateway. When credit runs out, a provider is down or a request times out, AIFRED moves to the next entry, with cooldowns, health status and usage tracking.
- **Curated memory:** small, bounded `MEMORY.md` and `USER.md` that the agent maintains, plus full-text search over past sessions.
- **Skills that learn:** file-based skills (`SKILL.md`) and a background review that proposes new memories and skills, with optional approval before anything is saved.
- **Tools and MCP:** built-in toolsets (memory, search, skills, web, files, optional terminal) and a built-in MCP client (Streamable HTTP, current and earlier protocol revisions, HTTP and HTTPS, optional certificate skip).
- **Instructions for every tool:** tell the agent what each MCP server or toolset is *for* ("`linux-mcp` is my local Linux servers"), so it picks the right one on its own. A Suggest button drafts the text from the server's discovered tools.
- **Safe by default:** single admin account, scrypt password hashing, session cookies with CSRF, hashed API keys, secrets encrypted at rest and never returned by the API, non-root read-only container. The master key for stored secrets lives in an OpenBao server next to the container, not on the data volume.

## Architecture at a glance

```
 CLI ─┐
 Telegram ─┤
 OpenAI API ─┼──►  Agent core  ──►  LLM router (routes, fallback)  ──►  providers
 Web UI ─┘         │
                   ├── Tool registry ── built-in toolsets, MCP servers, sub-agents
                   ├── Curated memory, skills, instructions
                   └── SQLite (WAL): sessions, FTS5 search, settings, journal
```

One process (`aifred serve`) hosts all gateways. State lives in a single `/data` volume.

## Quick start

```bash
make docker/run        # = scripts/setup.sh; safe to repeat
# open http://127.0.0.1:8700 and log in with the user and password it prints (once)
```

The script starts [OpenBao](https://openbao.org) (the key for stored secrets lives there, not on the data volume), initialises and unseals it, builds and starts AIFRED, and removes the bootstrap password from `.env`. After a restart of OpenBao run `make unseal`. LLM providers and keys, Telegram, MCP servers, tools and every other setting are configured from the dashboard and stored in the database. Full guide, backup and recovery: [docs/deploy.md](docs/deploy.md).

## Repository

| Path | Content |
|---|---|
| `docs/` | Technical specification |
| `brand/` | Logo, icons, favicon and design tokens (`brand/build.py` regenerates them) |
| `aifred/` | Python package (phases 1-3: configuration, storage, secrets, authentication, LLM providers and routes with failover, agent loop, memory, skills and tools) |
| `Dockerfile`, `docker-compose.yml`, `deploy/`, `scripts/`, `Makefile` | Deployment (see [docs/deploy.md](docs/deploy.md)); `make help` lists the commands |
| `tests/` | pytest suite |

## Development

Requires Python 3.13.

```bash
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -e '.[dev]'
.venv/bin/pytest

# run locally with a throw-away data directory
AIFRED_DATA_DIR=./data AIFRED_ADMIN_USER=admin AIFRED_ADMIN_PASSWORD='at least 12 chars' \
  .venv/bin/python -m aifred serve          # http://127.0.0.1:8700/health

# reset the admin password (reads it interactively, or from stdin with --password-stdin)
AIFRED_DATA_DIR=./data .venv/bin/python -m aifred admin reset-password
```

Bootstrap variables: `AIFRED_ADMIN_USER`, `AIFRED_ADMIN_PASSWORD` (see `.env.example`). `AIFRED_DATA_DIR` (default `/data`) and `AIFRED_API_HOST` (default `127.0.0.1`, `0.0.0.0` in the image) are image-level; the port is fixed at 8700. Implementation status and open decisions are tracked in [docs/analysis.md](docs/analysis.md).

## Roadmap

v0.1 covers everything above. Later candidates: sub-agents, an LLM-assisted editor for instructions, stdio MCP servers and OAuth, scheduled tasks, webhooks, multi-user accounts, more gateways. See section 14 of the specification.

## Name and license

AIFRED: your personal butler, with the **I** as the stem of the **A**. License to be decided before the first release.