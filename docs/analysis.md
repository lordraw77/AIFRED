# AIFRED — specification analysis and implementation plan

Companion to [specs.md](specs.md). It records the inconsistencies found in the specification, how they were resolved, and the phased implementation plan. On 25 October 2026 the open decisions D1–D3 were settled and `specs.md` was amended accordingly.

Tier: **M** (stateful service with persistence, authentication and several gateways; single process, single user).

## ASSUMPTIONS

| Id | Assumption | Affects |
| --- | --- | --- |
| A1 | Resolved by D1: sub-agents and the description assistant are roadmap (specs 14.1, 14.2). Instructions (9.3) stay in v0.1. | Phase 8 |
| A2 | The process listen port is an image constant (8700); `API_PORT` from 15.1 is dropped. `AIFRED_DATA_DIR` and `AIFRED_API_HOST` remain as image-level variables, not user configuration. | Phase 1, Docker |
| A3 | `cryptography` is a required dependency (4.2) in addition to the five listed in section 2. | Phase 1 |
| A4 | The dashboard authenticates with the `aifred_session` cookie and CSRF token (4.1); the "API token in `localStorage`" wording of 10.4/10.5 is obsolete. A 401 redirects to the login page. | Phase 6 |
| A5 | MCP header values are stored encrypted in the database; `${VAR}` expansion from the environment is dropped. | Phase 5 |
| A6 | Schema migrations are forward-only and versioned with `PRAGMA user_version`; rollback is done by restoring the `/data` volume backup. | All phases |
| A7 | Server-side login sessions are stored in an `auth_sessions` table (needed so logout invalidates them). | Phase 1 |
| A8 | `X-AIFRED-Session` and the `user` field are always namespaced under `api:`. | Phase 4 |
| A9 | Resolved by D2: the open Telegram bot is kept, with no memory or profile (not injected, no tool), `session_search` limited to its own chat, read-only skills, web only. | Phase 4 |
| A10 | A local knowledge base (RAG) is in the v0.1 scope, as requested on 26 October 2026 (specs 9.4). It is built after the agent core and before the gateways need it: its dashboard page belongs to phase 6 and its `/kb` commands to phase 4. | Phase 3b, 4, 6 |
| A11 | Everything stays in the existing SQLite file and container: no vector database, no new service. Vectors are searched exactly with `numpy` (ADR-003, accepted on 26 October 2026, D5). | Phase 3b, Docker |
| A12 | Embeddings come from the OpenAI-compatible `/embeddings` endpoint of a configured provider. No provider is assumed; a local model (Ollama, vLLM) is recommended so that documents never leave the machine. Which providers expose that endpoint is `[VERIFY]`. | Phase 3b |
| A13 | Only the admin adds, changes or deletes documents (dashboard, API, a watched folder, URLs). The agent can search but never write, and the open Telegram bot has no access to the knowledge base. | Phase 3b, 4 |
| A14 | The knowledge base is built in two steps. Step one (phase 3b) ingests text formats. Step two (phase 3c, D6) adds PDF, Office documents and images; images and scans are read by a vision model, parsing is sandboxed, and the two extra dependencies are proposed in ADR-004 (D9). | Phase 3b, 3c |
| A15 | The embedding model is chosen by the user (global setting, per-collection override); the specification recommends none (D7). | Phase 3b |
| A16 | Memory for the knowledge base is configurable and larger by default: `RAG_CACHE_MB` 512, `RAG_MAX_CHUNKS` 100,000, container `AIFRED_MEM_LIMIT` 1 GB instead of 512 MB. Searches never exceed the cache: larger collections are scanned in blocks. | Phase 3b, Docker |

## Inconsistencies in specs.md (all resolved by D3)

Section numbers below are those of the specification as it was reviewed. Sections 9.4 and 9.5 then meant sub-agents and the description assistant, which now live in 14.1 and 14.2; today 9.4 is the local knowledge base.

| # | Issue | Sections |
| --- | --- | --- |
| 1 | Sub-agents are out of scope in 1 and roadmap in 14, yet fully specified in 9.4, in the v0.1 acceptance criteria and in the README; `delegation` is "roadmap" in 9.1. | 1, 9.1, 9.4, 13, 14 |
| 2 | `cryptography` is required by 4.2 but missing from the dependency list. | 2, 4.2 |
| 3 | "The environment holds only the admin account" conflicts with `API_PORT` (15.1), `AIFRED_DATA_DIR`/`AIFRED_API_HOST` (12), `DATA_DIR` (8.1) and `${VAR}` MCP headers read from the environment (9.2). | 4, 8.1, 9.2, 12, 15 |
| 4 | `INSTRUCTIONS_BUDGET`, `AGENT_MAX_ITERATIONS`, `AGENT_TIMEOUT`, `AGENT_MAX_PARALLEL`, `AGENT_MAX_DEPTH` are missing from the settings table. | 4, 9.3, 9.4 |
| 5 | The `assist` route is not among the named routes. | 6.1, 9.5 |
| 6 | `tool_help`, `delegate`, `delegate_many` and the `agents` toolset are missing from the tool and toolset tables. | 9, 9.1 |
| 7 | The CLI command list lacks `/model`, `/approve`, `/reject`, `/journey delete`, `/instructions`, `/agent(s)`, `/assist`, `/mcp add`. | 10.1 |
| 8 | Dashboard auth: cookie + CSRF in 4.1 versus API token in `localStorage` in 10.4/10.5. | 4.1, 10.4, 10.5 |
| 9 | The `reset` event and notification events are missing from the agent event table. | 6, 6.1 |
| 10 | The sidebar lacks Models, API keys, Account and Agents; "Archived" and "Automation" have no backing in the schema. | 5, 10.5 |
| 11 | The assistant "needs no tool calling", but Evaluate dry-runs tool selection. | 9.5 |
| 12 | The non-streaming API reports zero `usage` although `llm_usage` has real counts. | 6.1, 10.3 |
| 13 | Mixed list markers and numbering left over from merged drafts. | various |

## Design and security gaps

1. **Open Telegram bot:** the "safe" toolsets include `memory` and `session_search`, which operate on the single admin's memory and history. Anyone could read past admin conversations or poison `USER.md`/`MEMORY.md` (see A9, D2).
2. Several allowlisted Telegram users share a single `USER.md`.
3. CLI as a separate process: the session lock is in-process only, the background review has no defined home in the synchronous REPL, MCP connections are duplicated, and live-applied settings need a reload mechanism (the CLI re-reads settings from the database at each turn).
4. History is trimmed by message count, not tokens, although `context_window` is known.
5. Behind a reverse proxy the per-IP rate limit and the `Secure` cookie need trusted-proxy configuration.
6. `POST /api/restart` in a read-only container needs a defined mechanism (in-process gateway restart).
7. Sequential versus parallel execution of multiple tool calls in one turn is unspecified.

## Decisions (25 October 2026)

| Id | Decision |
| --- | --- |
| D1 | Sub-agents and the description assistant move to the roadmap. |
| D2 | The open Telegram bot is kept, restricted as in A9 (stricter than first proposed: the admin's memory is neither readable nor writable, since the prompt snapshot would otherwise leak it). |
| D3 | `specs.md` amended: inconsistencies 1–13 are resolved in the specification. |
| D4 | The project rule on logs is relaxed: only passwords and other secrets must never be logged; IP addresses may appear (uvicorn access log enabled). |

## Implementation plan

| Phase | Content | Status |
| --- | --- | --- |
| 1 | Foundations: `config`, `store` (schema, FTS5, kv, journal, pending writes), secrets at rest, admin and auth, API keys, settings API, FastAPI skeleton with `/health`, `admin reset-password` command | done |
| 2 | LLM: SSE client, `<think>` filter, providers and models, route walker with failover, fake LLM | done |
| 3 | Agent: loop, system prompt, memory, skills, tool registry, built-in toolsets with security tests | done |
| 3b | Local knowledge base, step one (specs 9.4): migration for the `rag_*` tables, chunker, embedding client with retry and budgets, per-collection embedding model and test, memory cache with block-scan fallback, ingestion worker, hybrid search, `knowledge_search`/`knowledge_sources`, admin API, fake `/v1/embeddings` in the tests. The dashboard page comes with phase 6, the `/kb` commands with phase 4 | done |
| 3c | Local knowledge base, step two (specs 9.4, ADR-004): sandboxed parser process, PDF with page numbers, docx/xlsx/pptx/odt/ods/odp, JPG/PNG/WebP and scanned pages through the vision route, metadata stripping, vision model test, daily limit, hostile-file tests | todo |
| 4 | Gateways: `/v1` API, CLI, Telegram, shared slash commands, web chat endpoint; `/approve` and `/reject` | done |
| 5 | MCP client and fake MCP server (both protocol generations, TLS): migration 4, `mcp_client.py`, `mcp.py`, `/api/mcp/*`, `/mcp` command, `tests/fake_mcp.py`. `/mcp add` and the *What is this for?* field come with phase 8, the dashboard page with phase 6 | done |
| 6 | Dashboard: login, first-run guidance, Chat, Sessions, Memory, Skills, Knowledge, Tools, MCP, Models, Journey, Logs, Config, API keys, Account, System; static serving and every endpoint they need. The Instructions editors belong to phase 8 | done |
| 7 | Self-improvement: background review, `/refine`, `/journey`, save notices (the scanner moved to phase 3, `/approve` to phase 4, the Journey page to phase 6) | done |
| 8 | Instructions: `instructions` table, Capabilities section with budget, `tool_help`, Suggest (draft shown, never saved), required *What is this for?* for new MCP servers, `/instructions`, `/mcp add`, review proposals. The Generate/Improve/Shorten/Evaluate assistant stays on the roadmap (D1) | done |
| 9 | Docker/compose (done, brought forward) and end-to-end tests (todo; need the gateways) | partly done |

## Phase 2 notes

- Delivered: migration 2 (providers, keys, models, routes, health, usage), `llm.py`, `providers.py`, `router.py`, `store_llm.py`, dashboard API in `gateways/api_llm.py`, `tests/fake_llm.py` (a real-socket fake server so streaming, stalls and dropped connections are exercised), 218 tests in total.
- Async tests use the anyio pytest plugin that already ships with Starlette; no test dependency was added.
- Tests of the failover table were mutation-checked (cooldown base, key rotation, fatal-no-failover): each defect is caught.
- Not yet verified against real providers: preset URLs, `reasoning_effort` values, `stream_options` support per provider. They are marked `[VERIFY]` in the code and the specification and need a live check before release.
- Wiring into the agent (events `reset` and `notice`, the first-run pointer on `NoProvider`) happens in phase 3.

## Phase 2: live check against real providers (25 October 2026)

| Provider | Models listed | Tool-calling test | Note |
| --- | --- | --- | --- |
| Ollama | 13 | pass (`qwen3:8b` 74 s cold, `qwen3.5:2b`) | local engines need a longer first-token timeout |
| Groq | 11 | pass (`openai/gpt-oss-20b`) | |
| Mistral | 53 | pass (`ministral-14b-2512`) | `mistral-small-latest` hit a rate limit |
| NVIDIA | 85 | pass (`meta/llama-3.2-11b-vision-instruct`) | several catalogue models answer 404 "not found for account" |
| Cloudflare | 39 | pass (`@cf/meta/llama-3.3-70b-instruct-fp8-fast`) | catalogue comes from `/ai/models/search` |
| OpenRouter | 467 | not run to completion | free models: daily limit reached (needs credits) |
| Cerebras | 2 | not run to completion | HTTP 402, billing not enabled |
| Hugging Face | not reachable with the given token | not run | token expired; the public model list needs no key |
| Gemini | 60 | not run | paid; kept for last, no chat call made |

Defects found by the live run and fixed: Cloudflare model listing; read timeout of the HTTP client cutting `first_token` short; reasoning-only chunks not counted as activity; structured 404 treated as unavailable instead of model missing.

## Phase 3 notes

- Delivered: `scanner.py`, `memory.py`, `skills.py`, `agent.py`, `tools/registry.py`, `tools/builtin.py`, `tools/web.py`; session search can be scoped by session-id prefix. 401 tests in total (179 new).
- The scanner was brought forward from phase 7 so that no unfiltered write to memory or skills ever exists. The approval queue is filled now (`WRITE_APPROVAL` is honoured); applying a queued write arrives with `/approve`, delivered in phase 4.
- Open Telegram bot (D2) is enforced in the agent and the registry: no memory in the prompt, no `memory` or `skill_save` tool, own-chat search. The Telegram gateway (phase 4) only has to pass `restricted=True`.
- Security tests were mutation-checked: removing the workspace confinement, the SSRF address check, the redirect re-check, the restricted tool filter, the memory-in-prompt guard, the search scope or the scanner each fails at least one test.
- Defects found while testing: `file_read` could return more than `TOOL_OUTPUT_LIMIT`, cutting off its own paging note (now sized to fit); the scanner missed "API key" written with a space; the shutdown blocklist matched ordinary words such as `echo reboot` (now limited to command position).
- Test infrastructure: the anyio marker must be set with a module-level `pytestmark` (a marker added at collection time breaks parametrized async tests).
- Known limit to keep in mind for phase 9: with the terminal enabled the command can read `/data/.secret.key`. Mitigation options: a separate unprivileged user for commands, or keeping the key outside the volume path the terminal can see.

## Secrets backend and database (25 October 2026)

- Requested: move `secret.key` into a secure store next to the container, and evaluate PostgreSQL. Decisions are in [adr.md](adr.md): ADR-001 OpenBao Transit (Valkey rejected for this job), ADR-002 keep SQLite.
- Delivered: `vault.py` with two backends and a migration, `hardening.py`, `AIFRED_SECRETS_BACKEND` and `AIFRED_OPENBAO_*` settings, `admin migrate-secrets`, `GET /api/system/vault`, outage handling in the router, providers and settings, 28 offline tests plus 6 optional real-server tests.
- Verified live against OpenBao 2.4.0 in a throw-away container (removed afterwards).
- Defect found by the live run: none in the code; the real server confirmed the status codes the fake mirrors (403, 400, 503). One test assumption was wrong and fixed: `/proc/<pid>/environ` shows the environment at process start, not later changes.
- Open for phase 9: compose file with the OpenBao service, an init script (policy, AppRole, Transit key) and the unseal strategy, which is the operator's decision (see ADR-001).

## Docker deployment (25 October 2026)

- Delivered: `Dockerfile` (two stages, digest-pinned base, hash-locked dependencies via `requirements.lock`), `.dockerignore` (allow-list), `docker-compose.yml` (aifred, openbao, optional searxng), `deploy/`, `scripts/` (setup, unseal, backup, restore, break-glass root token, release), `Makefile`, `docs/deploy.md`, `ruff` as a dev dependency (needed by `make lint`; the standard library only checks syntax).
- The unseal strategy left open in ADR-001 now has a default: the key is kept in `openbao-unseal.key` (outside the volume, ignored by git) and `make unseal` uses it; deleting the file switches to pasting the key. Multi-share keys are supported (`OPENBAO_KEY_SHARES`).
- Verified on the Docker host in an isolated project (`aifred-deploytest`, removed afterwards): clean setup, setup repeated with no change, resume after a failed first run, healthy containers as uid 10001 / uid 100 with no capabilities and read-only roots, OpenBao without a published port and without internet, encrypted secrets with no key file, sealed → unseal flow, backup and restore of both volumes, SearXNG JSON search, down/up persistence, real-server test suite (6/6) on OpenBao 2.7.1.
- Defects found by running it for real, all fixed: the `file` storage type does not exist in OpenBao 2.7 (now Raft); audit devices must be declared in the config; unauthenticated root generation is off by default (break-glass script added); `init: true` exposed the AppRole credentials through `/proc/1/environ` (AIFRED is now PID 1, hardened, with an orphan reaper tested in a PID namespace); a first run that failed after generating the admin password lost it on the second run (now always shown); a silent exit in `setup.sh` from `pipefail` (now reports); `docker compose` refused to start because of a required variable of an inactive profile; `httpx` logged request URLs at INFO.
- Not covered: end-to-end tests of the gateways (phase 4+), TLS between the containers (documented as optional), log rotation of the OpenBao audit log.

## Local knowledge base (RAG), 26 October 2026

Requested: manage a local RAG. The design is in [specs.md](specs.md) section 9.4 and ADR-003 in [adr.md](adr.md); this section records the reasoning, the open points and the plan.

### What was decided in the specification

| Topic | Choice | Why |
| --- | --- | --- |
| Storage | `rag_collections`, `rag_documents`, `rag_chunks` and an FTS5 table in the existing SQLite file | One file to back up; no new service; the keyword side comes for free |
| Retrieval | Hybrid: exact cosine (dense) plus BM25, fused by reciprocal rank fusion, neighbours merged | Dense finds paraphrases, keywords find exact identifiers; fusion needs no score calibration; keywords still work when embeddings are down |
| Vector search | Exact, `numpy` matrix product (ADR-003, proposed) | Fast enough at personal scale, nothing to tune, no native extension |
| Embeddings | The user's configured provider and model (per-collection override); dimension fixed per collection; changing model marks the collection `stale` and re-embeds in the background | Vectors from different models are not comparable |
| Ingestion | Admin only; watched folder, upload, URL; one background worker; content hash makes it idempotent; only changed chunks are re-embedded; the old version stays searchable until the new one is complete | Cheap re-runs, no half-indexed documents, no self-poisoning by the model |
| Agent access | `knowledge_search` and `knowledge_sources`, toolset `knowledge`, off by default, never for the open Telegram bot | The documents are private |
| Safety | Excerpts framed as untrusted; ingest-time scan flags (does not block) suspicious documents | Documentation about prompt injection legitimately contains the phrases a blocker would reject |

### Design and security gaps found while specifying

8. **Indirect prompt injection:** a document, once indexed, is attacker-controlled text delivered by a trusted-looking tool. Mitigated by framing, flagging, admin-only ingestion and the toolset being off for open gateways; not eliminated.
9. **Privacy of cloud embeddings:** with a hosted embedding provider every chunk leaves the machine. Hence no default provider and a visible notice naming the receiver.
10. **Memory:** a collection's matrix costs 3 MB per thousand 768-dimension chunks (307 MB at the new default cap of 100,000). It is cached under `RAG_CACHE_MB` (512 MB) and larger collections are scanned in blocks, so memory use is bounded and configurable; the container limit rises to 1 GB by default (`AIFRED_MEM_LIMIT`).
11. **SQLite write contention:** one connection with a lock serves chat and ingestion. Ingestion must embed outside the lock and commit per document in short transactions, or a large import would stall the conversation.
12. **Quality is unmeasured:** without a set of golden questions nobody can tell whether a change to chunking or fusion helped. A small evaluation set (questions with the expected source) is part of the test plan and a prerequisite for the reranking item on the roadmap.
13. **Re-embedding cost:** a model change re-embeds everything; on a paid provider that is money. The dashboard should show the chunk count and an estimate before it starts.
14. **Backup size:** the knowledge base makes `aifred-data` grow (about 55 MB per 10,000 chunks at 768 dimensions, an estimate).
15. **Hostile files (step two):** PDF, Office and image parsers are a vulnerability class of their own, and `pypdfium2` is native code. Mitigated by a credential-free child process with limits and archive defences; the container remains the real boundary (ADR-004).
16. **Images leave the machine:** a hosted vision model receives them (with metadata stripped). Hence no default provider, a daily limit, and a notice naming the receiver. A paid provider must stay last in the route.
17. **Machine reading is fallible:** a transcription can invent or drop characters (commands, numbers). Excerpts are labelled `machine-read`, and anything safety-relevant should be checked against the original, which the dashboard keeps and shows.
18. **Typographic injection:** instructions written inside an image defeat any check made before reading. The extracted text is scanned after the vision model has produced it.
19. **Container memory:** parsing children and the vector cache both live inside the container limit; the defaults (1 GB container, 512 MB cache, 256 MB per parser) must be checked together when changed.

### Decisions (26 October 2026)

| Id | Question | Outcome |
| --- | --- | --- |
| D5 | Exact search with `numpy`, `sqlite-vec` or a pure-Python scan? | **`numpy`**, accepted (ADR-003) |
| D6 | PDF and Office files in v0.1? | **A second step**, phase 3c, which also adds images (JPG, PNG, WebP); the first step stays text-only |
| D7 | Which embedding model? | **The user's choice**: a global setting with a per-collection override; no recommendation in the specification. A live check on the project's Ollama is optional, to help choosing |
| D8 | Should the open Telegram bot ever see a collection? | **No for now; roadmap** (per-gateway collection access) |
| memory | Memory budget of the knowledge base | **Larger by default and configurable** (A16) |

Still open:

| Id | Question | Recommendation |
| --- | --- | --- |
| D9 | Add `pypdf` and `pypdfium2` (ADR-004)? | Yes. `pypdf` is pure Python; `pypdfium2` is needed only for scanned PDFs and can be dropped, in which case scans are reported as "needs OCR". Check wheel sizes and arm64 availability first `[VERIFY]` |
| D10 | Defaults of the vision route: the name `vision`, a daily limit of 200 images, no default provider | Keep. Put a local vision model first and any paid provider (for example Gemini) last |
| D11 | Which vision-capable model? | Same approach as D7: the user picks one; the vision test in the model editor checks that it works |

### Plan for phase 3b (step one)

1. Migration 3 and `store_rag.py`; unit tests for the queries and the cascade.
2. Chunker with tests (headings, fences, tables, overlap, unicode, long lines).
3. Embedding client on the provider layer (keys, TLS, retry, budgets, `llm_usage`), plus the fake `/v1/embeddings` endpoint.
4. Ingestion worker: idempotency, incremental embedding, atomic swap, `error` and `waiting` states, limits.
5. Search: dense, keyword, fusion, neighbour merge, fallback and `stale` handling; the golden-question set.
6. Tools, system-prompt Capabilities entry, restricted-gateway tests, admin API with authentication and CSRF tests.
7. Live check against the project's Ollama (embedding model choice, D7), then documentation and the acceptance criteria 16 to 20.

The dashboard page (phase 6) and the `/kb` commands (phase 4) follow their phases. Rough size: comparable to the LLM layer of phase 2 without the failover logic.

### Plan for phase 3c (step two)

1. The parser process: a small entry point that reads a file, applies the time, CPU and memory limits, returns text blocks as JSON, and never receives credentials. Tests for timeout, memory limit and crash.
2. Archive and XML defences for Office files (entry count, uncompressed size, compression ratio, `..` paths, DOCTYPE and entity rejection) and the parsers for docx, xlsx, pptx and the OpenDocument trio.
3. PDF text extraction with page numbers, encrypted-file refusal, the scan heuristic and page rendering.
4. Image type detection by bytes, EXIF and text-chunk stripping for JPEG, PNG and WebP, size limits.
5. The vision call on the provider layer (image content parts, the `vision` route, cache by hash, daily limit, `llm_usage`), the `machine-read` label and the `partial` state.
6. The vision test and the embedding test in the model editor (generated one-colour image; related and unrelated sentences).
7. Hostile-file tests (zip bomb, parser spin, entity expansion) and acceptance criteria 21 to 26.

Dependencies to confirm first: D9 (ADR-004).

### Not covered

Legacy Office formats, a local OCR engine, image and audio embeddings, reranking, approximate indexes, per-gateway collections and a folder watcher are on the roadmap (specs 14). Nothing in this change is implemented yet: it is specification and analysis only.

## Phase 3b notes: local knowledge base, step one (26 October 2026)

- **Delivered:** migration 3 (`rag_*` tables, FTS5 index, model `kind` and `dim`), `chunking.py`, `embeddings.py`, `rag.py` (collections, folder scan, URL ingestion, ingestion worker, hybrid search, vector cache), `store_rag.py`, `gateways/api_rag.py`, the `knowledge_search` and `knowledge_sources` tools, the prompt section, embedding-model test, `numpy` 2.5.4 (pinned and hash-locked). Tests: 134 new, 566 passing in total (6 more are skipped unless a real OpenBao is configured).
- **Verified:** the Docker image builds from the hash-locked file with `numpy` (235 MB) and a document dropped with `docker cp` is scanned, indexed and searched inside the read-only container as the unprivileged user with a 1 GB limit. Exact search of 50,000 × 768 vectors measured at 31 ms.
- **Mutation checks:** 12 deliberate defects in the fusion, the reuse of embeddings, the similarity floor, flagging, the scan's symlink guard, the upload limit, cache invalidation, the SSRF guard, the toolset gate, the retry rules and the section boundaries. 11 were caught at once. One survived (forcing the cache even over budget) because the old test only compared results; the test now asserts that no matrix is ever built for a collection that does not fit, and catches it.
- **Live check on the project's Ollama** (6-document corpus, 8 English and Italian questions; `recall@3` is lenient on so small a corpus):

| Model | Dimension | Indexing | recall@3 hybrid / dense / keyword | Top-1 correct (hybrid) |
| --- | --- | --- | --- | --- |
| `nomic-embed-text` | 768 | 2.5 s (warm) | 8 / 8 / 7 | 6 of 8 |
| `bge-m3` | not recorded | 75 to 158 s, including the cold load | 8 / 8 / 7 | 7 of 8 |

  Direct probe of `nomic-embed-text`: related sentences scored 0.95, an unrelated one 0.34. No model is recommended by the specification (D7); these are data for the user's choice. `all-minilm` was not run (English-only).
- **Defects found while building or checking:** a malformed embeddings answer raised an unhandled exception instead of a clean error; function words (`la`, `l`, `how`, `I`) in the keyword query outranked real matches in a small corpus (now filtered; identifiers are kept); a cold model load exceeded a 120 s request timeout (now `min(provider total, max(2 × first-token, 60 s))`, and a timeout is retried, which is what rescued the first indexing run on `bge-m3`); the first versions of two tests did not test what they claimed (a NaN the fake server could not send, a forced cache that left no trace).
- **Deviations from the written design, recorded in specs section 17:** staleness is tracked per document, not per collection; the upload is a raw request body, not multipart; models carry a `kind`.
- **Known limit:** a question like "what happens when the power fails" can still rank the wrong document first when the model is weak on it (seen with `bge-m3` on the Italian phrasing); the hybrid result had the right document in the top three. This is what the golden-question set and the roadmap's reranking item are for.
- **Not done in this step:** the dashboard page (phase 6), the `/kb` commands (phase 4), PDF/Office/images (phase 3c, awaiting D9 to D11).

## Phase 4 notes

- Delivered: `commands.py` (one command layer for every gateway; the open Telegram bot gets a restricted subset), `writes.py` (guarded writes shared by the tools and `/approve`), `gateways/api_openai.py`, `api_chat.py`, `cli.py`, `telegram.py`. The agent takes an optional `route` (the `model` field of `/v1`) and reports usage and the model in `DoneEvent`.
- **`/v1/chat/completions`.** Bearer key with role `chat` or `admin`; errors use the OpenAI error format; image content is rejected; the session is the `X-AIFRED-Session` header, then `user`, then `default`, always under `api:`. Streaming waits up to 15 s for the first event, so a quick failure still gets a proper HTTP status, then sends keep-alive comments. Usage is real, not estimated.
- **Web chat.** `POST /api/chat` (admin session) streams the agent events as SSE; a slash command answers with one `command` event. Sessions are `web:<id>`; the turn is cancelled when the client disconnects.
- **CLI.** `python -m aifred cli [--session ID] [-m TEXT]`; configuration is reloaded every turn, so dashboard changes apply to a running session. Ctrl-C during a turn interrupts it, twice at the prompt exits.
- **Telegram.** Long polling with no SDK. The allowlist answers a stranger with their own numeric id; an empty list makes the bot open (restricted, 20 messages a minute per user, startup warning). Private chats only. The session of a chat is kept in the key-value table and `/new` replaces it; the update offset is stored too, so a restart does not replay messages. Errors back off from 1 s to 30 s, honour `retry_after`, wait 15 s on a 409 (another poller) and stop on 401/404. The token is scrubbed from every error text. The gateway restarts by itself when the token, the allowlist or the API base change.
- Mutation-checked: removing the allowlist test, the restricted flag, the rate limit, the private-chat filter or the token scrubbing each fails a test.
- 658 tests in total.

