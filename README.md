<p align="center">
  <img src="brand/lockup.svg" alt="AIFRED" height="72">
</p>

<p align="center"><strong>A lean, self-hosted personal AI agent. Python, Docker, your models, your data.</strong></p>

<p align="center">
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-blue.svg"></a>
  <a href="https://hub.docker.com/r/lordraw/aifred"><img alt="Docker image" src="https://img.shields.io/docker/v/lordraw/aifred?label=docker&sort=semver"></a>
  <img alt="Python 3.13" src="https://img.shields.io/badge/python-3.13-blue.svg">
</p>


## What is AIFRED?

AIFRED is a small, from-scratch personal agent. It keeps the parts that matter (persistent memory, skills that improve over time, tools, several chat gateways) and drops the weight: one Python process, one SQLite database, one Docker container.

It runs on your hardware, talks to the LLMs you choose (local or hosted), and is configured entirely from a web dashboard.

**Status:** early release (v0.2.1). It is tested (970+ automated tests, with the security rules checked by breaking them on purpose) and has been run against real services: a Telegram bot, three MCP servers, NVIDIA's hosted API and a local Ollama. Some paths are still only tested against fakes (servers that need headers, the newest MCP revision, the open Telegram bot, most providers); expect rough edges and please report them.

## Highlights

- **One agent core, four gateways:** CLI, Telegram (with an optional allowlist; an open bot is restricted), an OpenAI-compatible API (`/v1/chat/completions`, stateful sessions, streaming) and a web dashboard. The same slash commands (`/new`, `/model`, `/think`, `/kb`, `/approve`, `/refine`, `/mcp`, `/cron`, `/agent`, …) work everywhere.
- **A complete web dashboard:** chat with streaming and tool traces, sessions and search, memory, skills, knowledge base, tools, MCP servers, models and routes with health and usage, a journal with undo, live logs, configuration, API keys and account, in a dark and a light theme. Plain HTML, CSS and JS with no build step, behind a strict Content-Security-Policy.
- **Bring any LLM, configure it from the web:** Ollama and local OpenAI-compatible servers (vLLM, llama.cpp, LM Studio), plus Groq, OpenRouter, Google Gemini, Cloudflare Workers AI, Cerebras, Mistral, NVIDIA and Hugging Face. Any other OpenAI-compatible endpoint works as a custom provider.
- **Routes and automatic fallback:** ordered provider/model lists per gateway. When credit runs out, a provider is down or a request times out, AIFRED moves to the next entry, with cooldowns, health status and usage tracking.
- **Curated memory:** small, bounded `MEMORY.md` and `USER.md` that the agent maintains, plus full-text search over past sessions.
- **Local knowledge base (RAG):** your own documents, chunked and embedded into the same SQLite file, searched with hybrid (semantic + keyword) retrieval. Embeddings come from a model you choose, ideally a local one; only you can add documents, the agent can only search them. Reads text, PDF, Office and OpenDocument files, and images (JPG, PNG, WebP) and scanned pages through a vision model you choose, with metadata removed first and a daily limit; every file is parsed in a limited child process.
- **Skills that learn:** file-based skills (`SKILL.md`) and a background review that proposes new memories, skills and better tool instructions, with optional approval before anything is saved and an undo for every change.
- **Tools and MCP:** built-in toolsets (memory, search, skills, web, files, optional terminal) and a built-in MCP client (Streamable HTTP, current and earlier protocol revisions, HTTP and HTTPS, optional certificate skip).
- **Scheduled tasks:** "every weekday at 8, summarise the news": recurring prompts the agent runs by itself and delivers on Telegram. The model can propose a task, but it only exists once you approve it.
- **Sub-agents:** named specialists (a researcher, a sysadmin for your MCP servers) with their own role, model and limits that the main agent can hand tasks to; they never get more access than the chat that called them.
- **Instructions for every tool:** tell the agent what each MCP server or toolset is *for* ("`linux-mcp` is my local Linux servers"), so it picks the right one on its own. A Suggest button drafts the text from the server's discovered tools.
- **Safe by default:** single admin account, scrypt password hashing, session cookies with CSRF, hashed API keys, secrets encrypted at rest and never returned by the API, non-root read-only container. The master key for stored secrets lives in an OpenBao server next to the container, not on the data volume.

## Architecture at a glance

```
 CLI ─┐
 Telegram ─┤
 OpenAI API ─┼──►  Agent core  ──►  LLM router (routes, fallback)  ──►  providers
 Web UI ─┘         │
                   ├── Tool registry ── built-in toolsets, MCP servers
                   ├── Knowledge base (RAG): documents, images, scans
                   ├── Curated memory, skills, instructions
                   └── SQLite (WAL): sessions, FTS5 search, settings, journal
```

One process (`aifred serve`) hosts all gateways. State lives in a single `/data` volume.

## Quick start

Run the published image with the full stack (AIFRED and an OpenBao server that keeps the key for your stored secrets):

```bash
git clone https://github.com/lordraw77/AIFRED.git && cd AIFRED
cp .env.example .env                         # set AIFRED_ADMIN_USER and AIFRED_ADMIN_PASSWORD (12+ characters)
echo 'AIFRED_IMAGE=lordraw/aifred' >> .env
AIFRED_PULL=1 make docker/run                # pulls lordraw/aifred; without AIFRED_PULL it builds the image locally
# open http://127.0.0.1:8700 and sign in
```

The script starts [OpenBao](https://openbao.org), initialises and unseals it, starts AIFRED and removes the bootstrap password from `.env`. After a restart of OpenBao run `make unseal`. Then, on the dashboard: **Models** → add a provider (for example a local Ollama), press *Test* on a model and put it on the `default` route; everything else (Telegram, MCP servers, the knowledge base, tools) is configured on the dashboard too and stored in the database.

To try it without OpenBao, `docker run` instructions are in the [Docker Hub overview](docs/dockerhub.md). Full guide, backup and recovery: [docs/deploy.md](docs/deploy.md).

## Documentation

| | |
|---|---|
| [docs/deploy.md](docs/deploy.md) | Deployment, the published image, backup, recovery, the knowledge base and vision setup |
| [docs/specs.md](docs/specs.md) | The specification: design, data model, every feature, security, tests |
| [docs/adr.md](docs/adr.md) | Architecture decision records |
| [docs/analysis.md](docs/analysis.md) | Implementation status, resolved decisions and measurements |
| [CHANGELOG.md](CHANGELOG.md) | What changed in each version |
| [CONTRIBUTING.md](CONTRIBUTING.md) · [SECURITY.md](SECURITY.md) | How to contribute and how to report a vulnerability |

## Repository

| Path | Content |
|---|---|
| `aifred/` | The Python package: configuration, storage, secrets, authentication, LLM providers and routes, the agent, tools, MCP client, knowledge base, gateways (CLI, Telegram, API, web) and the dashboard in `aifred/web/static/` |
| `tests/` | pytest suite, with fake LLM, MCP, Telegram and OpenBao servers |
| `docs/` | Specification, decisions, analysis, deployment guide and the Docker Hub overview |
| `brand/` | Logo, icons, favicon and design tokens (`brand/build.py` regenerates them) |
| `Dockerfile`, `docker-compose.yml`, `deploy/`, `scripts/`, `Makefile` | Deployment; `make help` lists the commands |

## Development

Requires Python 3.13. Contributions are welcome: see [CONTRIBUTING.md](CONTRIBUTING.md).

```bash
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -e '.[dev]'
.venv/bin/ruff check .
.venv/bin/pytest                              # about 5 minutes; the 6 real-OpenBao tests are skipped without a server

# run locally with a throw-away data directory
AIFRED_DATA_DIR=./data AIFRED_ADMIN_USER=admin AIFRED_ADMIN_PASSWORD='at least 12 chars' \
  .venv/bin/python -m aifred serve          # http://127.0.0.1:8700/health

# reset the admin password (reads it interactively, or from stdin with --password-stdin)
AIFRED_DATA_DIR=./data .venv/bin/python -m aifred admin reset-password
```

Bootstrap variables: `AIFRED_ADMIN_USER`, `AIFRED_ADMIN_PASSWORD` (see `.env.example`). `AIFRED_DATA_DIR` (default `/data`) and `AIFRED_API_HOST` (default `127.0.0.1`, `0.0.0.0` in the image) are image-level; the port is fixed at 8700. Implementation status and open decisions are tracked in [docs/analysis.md](docs/analysis.md).

## Roadmap

v0.1 covers everything above. Later candidates: an LLM-assisted editor for instructions (generate, improve, shorten, evaluate), per-gateway access to knowledge-base collections, stdio MCP servers and OAuth, webhooks, multi-user accounts, more gateways, an arm64 image. See section 14 of the specification.

## Name and license

AIFRED: your personal butler, with the **I** as the stem of the **A**.

Released under the [MIT License](LICENSE): you may use, copy, modify, fork, redistribute and even sell it, as long as the copyright and license notice stay with it. Forks and pull requests are welcome. The Docker image is on Docker Hub: [`lordraw/aifred`](https://hub.docker.com/r/lordraw/aifred) ([overview](docs/dockerhub.md)).