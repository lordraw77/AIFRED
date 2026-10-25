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
- **Instructions for every tool:** tell the agent what each MCP server or toolset is *for* ("`linux-mcp` is my local Linux servers"), so it picks the right one on its own. An LLM-assisted editor drafts the text and scores it against generated test requests.
- **Sub-agents:** named agents with their own role, model route, skills and toolsets, delegated to by the main agent or called directly. They can never exceed the privileges of the gateway that called them.
- **Safe by default:** single admin account, scrypt password hashing, session cookies with CSRF, hashed API keys, secrets encrypted at rest and never returned by the API, non-root read-only container.

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

## Quick start (planned)

```bash
cp .env.example .env        # set AIFRED_ADMIN_USER and AIFRED_ADMIN_PASSWORD only
docker compose up -d --build
# open http://127.0.0.1:8700, log in, and follow the first-run wizard
```

The environment holds only the admin account. LLM providers and keys, Telegram, MCP servers, tools and every other setting are configured from the dashboard and stored in the database.

## Repository

| Path | Content |
|---|---|
| `docs/` | Technical specification |
| `brand/` | Logo, icons, favicon and design tokens (`brand/build.py` regenerates them) |
| `aifred/` | Python package (placeholder until implementation starts) |

## Roadmap

v0.1 covers everything above. Later candidates: stdio MCP servers and OAuth, scheduled tasks, webhooks, multi-user accounts, more gateways. See section 14 of the specification.

## Name and license

AIFRED: your personal butler, with the **I** as the stem of the **A**. License to be decided before the first release.