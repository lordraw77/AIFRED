# Changelog

All notable changes to AIFRED. The format follows [Keep a Changelog](https://keepachangelog.com/); the project is pre-release (`0.1.0.dev0`).

## [Unreleased]

### Added
- **Instructions (phase 8).** Each MCP server, tool group or tool can carry a note saying what it is for. The system prompt gets a Capabilities section from the notes of what is enabled for the gateway (shortened to `INSTRUCTIONS_BUDGET`), and the new `tool_help` tool returns the full text. *What is this for?* is required when adding an MCP server (dashboard, API, `/mcp add`); a Suggest button drafts it from the server's tools (shown, never saved until accepted); undocumented servers are flagged. `/instructions`, an editor on the MCP and Tools pages, and proposals from the background review that wait for approval.
- **Background self-improvement review (phase 7).** After a substantial turn a separate model call (the `review` route, capped by `REVIEW_MAX_INPUT_TOKENS`) re-reads the conversation and may save memory entries or skills with only the `memory` and `skill_save` tools, through the same safety filter, journal and approval queue as everything else. It runs on its own thread, never for the open Telegram bot, and never sees tool results. New commands `/refine` (review now) and `/journey [delete <id>]`; chat now says what was saved (`NOTIFICATIONS`).
- **Web dashboard, the other sections (phase 6).** Sessions (overview, history search, rename, export, delete), Memory, Skills (with a safe Markdown preview), Knowledge (collections, upload, URL, scan, re-index, test search), Tools, MCP, Journey (approval queue and undo) and Logs (live), plus a Restart button, `g` + letter shortcuts and read-only viewing of CLI and Telegram conversations. New endpoints for memory, skills, tools, journal and approvals, search, logs and restart.
- **Web dashboard, first slice (phase 6).** A single page of plain HTML, CSS and JS at `/` with no build step and a strict Content-Security-Policy: login, Chat (streaming, tool chips, commands), Models (providers, keys, model tests, routes, health, usage, getting-started checklist), Config (grouped, filtered, write-only secrets), API keys, Account and System, in the Teal and Sand themes with a mobile drawer. New endpoints `/api/status` and `/api/sessions`.
- **MCP client (phase 5).** Streamable HTTP over `httpx`, no SDK: the stateless 2026-07-28 revision (`server/discover`, `_meta`, `Mcp-Method`/`Mcp-Name`, `Mcp-Param-*` headers) with automatic fallback to the `initialize` handshake and sessions of 2025-03-26 to 2025-11-25. JSON and SSE answers, paginated and TTL-cached tool lists, per-server include/exclude/cap, TLS verification per server (on, off or a private CA), background connection with retry, journaled tool changes. Tools appear as `mcp__<server>__<tool>` in the `mcp` / `mcp:<name>` toolsets and never reach the open Telegram bot. Header values are encrypted in the secret store. `/api/mcp/*`, the `/mcp` command and a one-time `data/mcp.json` import.
- **Gateways (phase 4).** OpenAI-compatible `POST /v1/chat/completions` (streaming, real usage, sessions, per-request model), web chat endpoint `POST /api/chat`, command-line chat (`python -m aifred cli`), and a Telegram bot over long polling with an allowlist, an open restricted mode with rate limit, message splitting and offset persistence. One shared slash-command layer (`/new`, `/model`, `/think`, `/kb`, `/pending`, `/approve`, `/reject`, `/mcp`, …).
- **Approval queue.** Memory and skill writes held by `WRITE_APPROVAL` can now be applied or discarded with `/approve` and `/reject` (re-scanned before applying).
- **Local knowledge base, step one (phase 3b).** Chunking by headings and paragraphs, embeddings through any OpenAI-compatible `/embeddings` endpoint (model chosen by the user), hybrid search (exact cosine with numpy + FTS5 BM25, fused with reciprocal rank fusion), background indexing queue, bounded memory cache (`RAG_CACHE_MB`), `knowledge_search` / `knowledge_sources` tools, admin API. Off by default; never offered to the open Telegram bot.
- Specification and decisions for the knowledge base including PDF, Office and images (ADR-003, ADR-004 proposed), and analysis notes for phases 3b, 4 and 5.

### Changed
- Agent turns report token usage and the model used, and accept an explicit route.
- The tool registry supports dynamic tool sources and raw JSON schemas; each process gets its own registry.
- Default container memory limit raised to 1 GB and made configurable (`AIFRED_MEM_LIMIT`).
- Embedding models are kept apart from chat models (`kind`), and routes refuse them.

### Security
- Open Telegram bot: no memory, no knowledge base, no MCP, own-chat search only; the bot token is scrubbed from every error text.
- MCP: header values never leave the server; `verify_tls: false` is logged, journaled and flagged in the API.

## [0.1.0.dev0] - 2026-10-25
### Added
- Core: configuration in the database, admin account and API keys, LLM providers with routes, failover, key rotation and budgets, agent loop with memory, skills and built-in tools.
- Secrets in OpenBao (Transit) or an encrypted local file.
- Docker deployment with an OpenBao stack and operations scripts.
- Specification, architecture decisions and analysis.
