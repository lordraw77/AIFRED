# Changelog

All notable changes to AIFRED. The format follows [Keep a Changelog](https://keepachangelog.com/); the first release is `0.1.0`.

## [Unreleased]

### Added
- **Library.** Save the output of a chat or the result of an agent (*Save to library* under each answer, under agent results and on the Agents page) with a title and tags. When you ask for it later, chat and agents search the library (`library_search`, `library_get`) and give you the saved text instead of writing it again; the system prompt lists the latest titles. A Library page lists, searches, edits, copies and deletes items, and *Regenerate* asks again with the stored request and shows a draft you accept (the old text can be restored) or discard. The model saves with `library_save` only when asked; it is scanned and follows `WRITE_APPROVAL=all`. New toolset `library` (in the default list of new installations; add it to the gateways you want on the Library page), settings `LIBRARY_ITEM_CHARS`, `LIBRARY_MAX_ITEMS`, `LIBRARY_PROMPT_ITEMS`, API `/api/library`, database migration 13, and a Library section in the guide.

## [0.5.0] - 2026-10-28

### Added
- **Memory as a graph.** Names written between double brackets (`[[Marco]] works at [[Acme]]`) become dots on a graph under the two memory files; names that share an entry are joined. Click a dot to see its entries. Older entries join when they mention a name already in use. The files remain the source; `GET /api/memory/graph/view`.
- **`MEMORY_LIMIT` and `USER_LIMIT`.** The size of `MEMORY.md` and `USER.md` is now a setting (300 to 50,000 characters; defaults 2,200 and 1,375), with a link from each counter.

### Changed
- **Better extraction in the background review.** One fact per entry starting with its subject, absolute dates, the same spelling for a name, merge instead of near-duplicates, tidy-up above 85% full; the review sees how full each file is. A long conversation shortens the assistant's answers before dropping what the user said.

## [0.4.1] - 2026-10-28

### Added
- **Schedules in plain sentences.** The schedule field understands English and Italian: `every morning at 6`, `ogni mattina alle 6`, `dal lunedì al venerdì alle 8:30`, `every monday and thursday at 9am`, `ogni 2 ore`, `il primo del mese alle 8`… The form shows *Understood as 0 6 * * ** and the next runs before you save, and the task is stored as cron. Works in the dashboard, in `/cron add` and for tasks the assistant proposes. Incomplete sentences say what is missing instead of guessing.
- **A bilingual guide inside the dashboard.** A *Guide* entry in the menu and a *Guide* button on every page open one section per page (19 sections, English and Italian, with 30 screenshots) that explains every control, every setting and every command. The screenshots come from a demo instance (`scripts/guide-screenshots/`) so they can be redone; tests keep the guide honest (every page and every setting is covered, links and pictures exist, the schedule examples really mean what they say).

### Fixed
- Chat: a reply with one line per command (such as the answer to `/help`) lost its line breaks.

## [0.4.0] - 2026-10-27

### Added
- **Description assistant, rest.** Progress is streamed while a test runs (`POST /api/assist/evaluate/stream`), the `/assist <scope> [hint]` chat command drafts or improves an instruction and scores it without saving anything, and Generate/Improve can optionally use your profile and notes (names, address ranges) when you tick the box.
- **Knowledge base: reranking.** A rerank model (`RAG_RERANK_PROVIDER`, `RAG_RERANK_MODEL`, any `/rerank` endpoint: Jina, Cohere, Voyage, vLLM) re-orders the best 20 candidates; if it fails the normal order is kept and the result says so.
- **Knowledge base: evaluation.** Golden questions (written by hand or by the model from your documents) and a *Run evaluation* button that compares `hybrid`, `dense`, `keyword` and `hybrid+rerank` by recall and MRR and keeps the history to show whether a change helped. Migration 10.

- **MCP over stdio.** A server can be a command run as a child process instead of a URL. Off until you list the program in `MCP_STDIO_COMMANDS`; the program gets a minimal environment (only the variables set for that server, stored encrypted), its own private directory and process group, and is killed with everything it started. Command field on the MCP page. Migration 11.
- **MCP OAuth.** A web server can sign in with OAuth 2.1 (authorization code, PKCE, discovery, refresh): *Authorize* on the MCP page opens the provider and the tokens are kept encrypted. A client id registered by hand (`MCP_OAUTH_CLIENT_ID`) or, if AIFRED is reachable from the internet, a client metadata document that AIFRED serves (`MCP_OAUTH_PUBLIC_URL`). Migration 12.
- Not done: `subscriptions/listen` and `input_required` (elicitation, sampling) remain on the roadmap.

## [0.3.0] - 2026-10-27

### Added
- **Description assistant.** Next to every instruction on the Tools and MCP pages: Generate, Improve and Shorten, and **Test**, which dry-runs the model's tool choice on about 8 test requests (written by the model or by you) with the real tool definitions and shows a score, pass or fail per request and the score of the saved text; **Refine and test** lets the model rewrite the text up to 3 times and keeps the best. No tool is executed and no memory or profile is sent. Accepting a tested text journals its score. Improve and Generate also on a sub-agent's description and role and on a skill's description. Settings `ASSIST_EVALUATIONS_PER_HOUR`; API `POST /api/assist/evaluate` and `/api/assist/improve`.
- **Context compression.** When a conversation's history grows past `COMPRESS_TRIGGER_TOKENS` (12,000 estimated tokens) the older messages are condensed into notes that replace them in what the model receives; the newest `COMPRESS_KEEP_MESSAGES` (8) stay word for word and nothing is deleted from the record. Summaries are framed as context, never as instructions, are scanned for injected instructions and can use a cheaper model through `ROUTE_COMPRESS`. A failed summary leaves the conversation as it was. Database migration 9.
- Switches to turn a toolset on or off per gateway (CLI, Web, API, Telegram, Scheduled) or everywhere, so `TOOLSETS` and `TOOLSETS_<GATEWAY>` no longer have to be edited by hand: on the Tools page (every cell of the matrix, which now has a Scheduled column), on each MCP server, on the Agents page (delegation) and on the Schedules page (the `cron` tool). A gateway without its own list gets a copy of the shared one with the change, so the others do not move. `GET`/`PUT /api/toolsets/{name}`.

## [0.2.1] - 2026-10-27

### Added
- Chat: answers are rendered as Markdown (headings, lists, tables, quotes, code, bold and italic, and clickable `http(s)` links that open in a new tab; HTML is never rendered). Tool results, including sub-agent answers, show a ▸/▾ marker, stay collapsed until opened and are Markdown too (up to 6,000 characters instead of 500).
- Telegram: answers (and scheduled-task deliveries) are sent formatted: Markdown becomes Telegram HTML (bold, italic, code, links, bullets, headings in bold, tables as aligned text). Everything else is escaped and only `http(s)` links are kept; if Telegram refuses the markup the message is sent again as plain text.
- Chat: each conversation in the list has a delete button with a Yes/No confirmation. Deleting a conversation also deletes the sub-agent conversations it started.

## [0.2.0] - 2026-10-27

### Added
- **Scheduled tasks.** A prompt the agent runs on its own on a schedule (cron fields, `@daily`-style aliases or `every 30m`, in `SCHEDULER_TIMEZONE`) and delivers on Telegram. Runs are unattended: route `ROUTE_CRON`, narrow `TOOLSETS_CRON` (default search, web, skills), a time limit, no review, and they cannot create tasks. Slots are claimed in the database so they run once, overlapping and long-overdue slots are skipped and recorded. The `cronjob` tool (toolset `cron`, off by default) lets the model list, run, pause and resume tasks; creating, changing and removing wait for your approval with a preview of the next runs. Delivery only to chats on `TELEGRAM_ALLOWED_IDS`. Schedules page with a live preview, `/cron` command, `/api/schedules`.
- **Sub-agents.** Named agents with their own role, model route, skills, toolsets, memory mode and limits that the main agent hands tasks to with `delegate` and `delegate_many` (toolset `agents`, off by default) or you call with `/agent <name> <task>`. A run gets the intersection of the agent's toolsets and the caller's, never delegates further, is refused for open Telegram chats, runs in a child session and is recorded with tokens and output. Starter agents `researcher`, `sysadmin` and `writer` (off until you turn them on). Agents page with a test box and history, `/agents`, `/api/agents`.
- Settings `SCHEDULER_*` and `AGENT_*`, `ROUTE_CRON`, `TOOLSETS_CRON`; database migrations 7 and 8.

## [0.1.1] - 2026-10-27

### Fixed
- Telegram: the gateway reports `running` as soon as the bot token works instead of after the first long poll (up to 50 s).
- Vision: reading an image is slow on hosted free tiers and local CPUs, so vision calls and the vision test allow at least 300 s to the first token (180 s of silence, 900 s in total) unless a route entry sets its own timeouts.
- Vision test: the test picture uses four different digits and the model gets two tries, so a model that reads the picture is no longer failed for dropping a repeated digit.

### Added
- Setting `RAG_RENDER_MAX_SIDE` (default 1,600 pixels): the size of scanned PDF pages sent to the vision model; lower it for slow local models.

## [0.1.0] - 2026-10-27

First public release.

### Added
- **Project files.** `CONTRIBUTING.md` and `SECURITY.md`; the specification now holds the implementation details inside the sections they belong to instead of appended notes; the README is up to date.
- **License.** AIFRED is released under the MIT License (`LICENSE`), so anyone may use, modify, fork and redistribute it. A Docker Hub overview is in `docs/dockerhub.md`; the image labels now carry the license and the source and documentation links.
- **Docker Hub image.** `lordraw/aifred` (`0.1.0`, `latest`, linux/amd64): `AIFRED_IMAGE` and `AIFRED_PULL=1` make `docker compose` and `scripts/setup.sh` use it instead of building. The image no longer ships `pip`, carries the pending openssl security update and has no HIGH or CRITICAL finding with a fix in a Trivy scan.
- **Knowledge base, second step (phase 3c).** PDF (with page numbers in citations), docx, xlsx, pptx, odt, ods, odp, and JPG/PNG/WebP images; scanned PDF pages and images are read by a vision model on a dedicated `vision` route that has no default provider, needs *Test vision*, is capped by `RAG_VISION_DAILY_LIMIT` (200) and is never used for chat. Metadata (EXIF, GPS, XMP, comments) is stripped before anything leaves the machine; descriptions are cached by image hash; text read by a model is labelled `machine-read` and scanned for injected instructions. Every file is parsed in a limited child process (no credentials, time, CPU and memory limits, archive and XML defences). Documents that hit a limit are `partial`, not lost. Thumbnails and the original file on the Knowledge page. New dependencies `pypdf` and `pypdfium2` (ADR-004 accepted).
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

## [0.1.0.dev0] - 2026-10-25 (development snapshot)
### Added
- Core: configuration in the database, admin account and API keys, LLM providers with routes, failover, key rotation and budgets, agent loop with memory, skills and built-in tools.
- Secrets in OpenBao (Transit) or an encrypted local file.
- Docker deployment with an OpenBao stack and operations scripts.
- Specification, architecture decisions and analysis.
