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

## Inconsistencies in specs.md (all resolved by D3)

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
| 4 | Gateways: `/v1` API, CLI, Telegram | todo |
| 5 | MCP client and fake MCP server (both protocol generations, TLS) | todo |
| 6 | Dashboard: login, first-run wizard, Models, Config, Chat, then the other sections | todo |
| 7 | Self-improvement: background review, `/approve` and `/reject` for the approval queue, journey (the scanner moved to phase 3) | todo |
| 8 | Instructions (Capabilities section, `tool_help`, Suggest button) | todo |
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
- The scanner was brought forward from phase 7 so that no unfiltered write to memory or skills ever exists. The approval queue is filled now (`WRITE_APPROVAL` is honoured); applying a queued write arrives with `/approve` in phase 7.
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
