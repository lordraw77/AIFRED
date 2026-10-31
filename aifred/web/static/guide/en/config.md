# Config

Every setting of AIFRED, in one page, grouped by topic. Changes apply at once, without restarting. Providers, MCP servers and their keys live on their own pages; everything else is here.

![Filtering the settings](/static/guide/img/config-1-filter.png)

## What you see

- **The filter box** (the `/` key jumps to it): shows only the settings whose name, description or group contain your words.
- **One panel per group**, each row with the **name**, a **description**, the **control** and, when the value differs from the default, `default: …` under it.
- **Controls**: a checkbox for on/off settings, a menu for choices, a number box, a text box (lists are comma-separated), a JSON box.
- **Secrets** (such as `TELEGRAM_TOKEN`) are **write-only**: the box says *set; type to replace* or *not set*, the value is never sent back to the browser, and *Clear* removes it.
- **The save bar** appears at the bottom with the number of unsaved changes: **Save** applies them all at once (if one value is invalid nothing is saved and you are told which), **Discard** reverts. Filtering with unsaved changes is blocked so none are lost.

## The settings

### llm
| Setting | Default | Meaning |
|---|---|---|
| `LLM_THINKING` | off | Reasoning mode for models that support it; `/think` changes it for one conversation. |
| `LLM_TEMPERATURE` | 0.6 | How adventurous the wording is (0 to 2). |
| `LLM_EXTRA_BODY` | {} | A JSON object merged into every request, for provider-specific options. |

### routing
| Setting | Default | Meaning |
|---|---|---|
| `ROUTE_CLI`, `ROUTE_TELEGRAM`, `ROUTE_API`, `ROUTE_WEB` | default | The route each gateway uses. |
| `ROUTE_CRON` | default | The route of scheduled tasks. |
| `ROUTE_COMPRESS` | empty | The route that writes conversation summaries (empty: the conversation's own; a cheaper model is enough). |
| `ROUTE_DEADLINE` | 120 | Seconds allowed to walk through a route. |
| `COOLDOWN_QUOTA` | 3600 | Seconds a model rests after its quota ran out. |

### agent
| Setting | Default | Meaning |
|---|---|---|
| `MAX_ITERATIONS` | 12 | Most tool rounds per request. |
| `HISTORY_LIMIT` | 40 | Messages of history sent to the model. |
| `MEMORY_LIMIT` | 2200 | Characters the assistant's notes (`MEMORY.md`) may use; sent with every request. |
| `USER_LIMIT` | 1375 | Characters the user profile (`USER.md`) may use; sent with every request. |
| `COMPRESS_ENABLED` | on | Summarise the older part of a long conversation. |
| `COMPRESS_TRIGGER_TOKENS` | 12000 | Estimated size that triggers a summary. |
| `COMPRESS_KEEP_MESSAGES` | 8 | Latest messages kept word for word. |
| `COMPRESS_SUMMARY_TOKENS` | 700 | Length limit of a summary. |
| `IDLE_MINUTES` | 30 | After this much silence the assistant's prompt is rebuilt. |
| `TOOL_OUTPUT_LIMIT` | 8000 | Most characters of a tool result passed to the model. |
| `INSTRUCTIONS_BUDGET` | 3000 | Most characters of the *Capabilities* section. |
| `ASSIST_EVALUATIONS_PER_HOUR` | 10 | Instruction tests the dashboard may run an hour. |

### tools
| Setting | Default | Meaning |
|---|---|---|
| `TOOLSETS` | memory, search, skills, web, file, library | The toolsets every gateway has unless it has its own list. |
| `TOOLSETS_CLI`, `TOOLSETS_TELEGRAM`, `TOOLSETS_API`, `TOOLSETS_WEB` | empty (inherit) | A gateway's own list. The switches on [Tools](#/guide?s=tools) and [MCP](#/guide?s=mcp) edit these for you. |
| `TOOLSETS_CRON` | search, web, skills | Toolsets for scheduled tasks; add more only for tasks you trust. |
| `ENABLE_TERMINAL` | off | Shortcut that adds `terminal` (shell commands). |
| `SSH_HOSTS` | empty | Hosts the `ssh` toolset may reach, a JSON object `{"name": {"user", "host", "port", "host_key", "description"}}`. `host_key` is required (`ssh-keyscan -t ed25519 host`). |
| `SSH_PRIVATE_KEY` | not set | Unencrypted private key used for every host in `SSH_HOSTS` (secret). |
| `SSH_TIMEOUT` | 60 | Remote command timeout (s). |
| `TERMINAL_TIMEOUT` | 60 | Seconds a command may run. |
| `WEB_ALLOW_PRIVATE` | off | Let the page fetcher reach addresses of your own network. |
| `SEARXNG_URL` | empty | Address of a SearXNG server; it turns web search on. |

### telegram
| Setting | Default | Meaning |
|---|---|---|
| `TELEGRAM_TOKEN` | not set | The bot token; it turns the Telegram gateway on. |
| `TELEGRAM_ALLOWED_IDS` | empty | Numeric ids allowed to talk to the bot. **Empty means open to everyone** (restricted mode). |
| `TELEGRAM_API_BASE` | api.telegram.org | Change only to use a local Bot API server. |

### scheduler
| Setting | Default | Meaning |
|---|---|---|
| `SCHEDULER_ENABLED` | on | Run scheduled tasks. |
| `SCHEDULER_TIMEZONE` | empty | Time zone of schedules, e.g. `Europe/Rome` (empty: the server's). |
| `SCHEDULER_MIN_INTERVAL` | 5 | Shortest gap, in minutes, between runs of one task. |
| `SCHEDULER_MAX_TASKS` | 20 | Most tasks. |
| `SCHEDULER_RUN_TIMEOUT` | 600 | Seconds one run may take. |
| `SCHEDULER_MAX_PARALLEL` | 2 | Runs at the same time. |

### agents
| Setting | Default | Meaning |
|---|---|---|
| `AGENT_MAX_ITERATIONS` | 8 | Tool rounds of a sub-agent (unless its own limit is set). |
| `AGENT_TIMEOUT` | 300 | Seconds of a sub-agent run. |
| `AGENT_MAX_PARALLEL` | 3 | Sub-agents running together in one delegation. |
| `AGENT_MAX_AGENTS` | 20 | Most sub-agents. |

### mcp
| Setting | Default | Meaning |
|---|---|---|
| `MCP_CONNECT_TIMEOUT`, `MCP_CALL_TIMEOUT` | 15, 120 | Default timeouts in seconds. |
| `MCP_MAX_TOOLS_PER_SERVER` | 40 | Most tools taken from one server. |
| `MCP_ELICITATION` | on | A 2026-07-28 MCP server may ask you for information in the middle of a tool call (a short form); the model asks you in the chat. Forms for passwords, tokens or keys are always declined. |
| `MCP_SAMPLING` | off | A 2026-07-28 MCP server may have AIFRED's model write text for it. It spends your tokens: text only, no tools. |
| `MCP_SAMPLING_PER_HOUR` | 20 | Sampling requests all servers together may make in an hour. |
| `MCP_SUBSCRIPTIONS` | on | Keep a `subscriptions/listen` stream open to 2026-07-28 servers so a changed tool list is read at once. |
| `MCP_STDIO_COMMANDS` | empty | Program names the dashboard may start as MCP servers. Empty: that feature is off. |
| `MCP_OAUTH_CLIENT_ID` | empty | Client id registered by hand for OAuth sign-in. |
| `MCP_OAUTH_PUBLIC_URL` | empty | Public https address of AIFRED, so it can serve its client metadata document. |

### library
| Setting | Default | Meaning |
|---|---|---|
| `LIBRARY_ITEM_CHARS` | 20000 | Largest item the [library](#/guide?s=library) accepts, in characters. |
| `LIBRARY_MAX_ITEMS` | 500 | Most items the library can hold. |
| `LIBRARY_PROMPT_ITEMS` | 30 | Latest titles listed in the system prompt (0: none; the assistant can still search). |

### knowledge
| Setting | Default | Meaning |
|---|---|---|
| `RAG_ENABLED` | off | Turns the knowledge base on. |
| `RAG_EMBED_PROVIDER`, `RAG_EMBED_MODEL` | empty | The embedding model (empty: keyword search only). Overridable per collection. |
| `RAG_RERANK_PROVIDER`, `RAG_RERANK_MODEL` | empty | An optional rerank model on a provider with a `/rerank` endpoint. |
| `RAG_RERANK_CANDIDATES` | 20 | Candidates the rerank model re-orders. |
| `RAG_CHUNK_CHARS`, `RAG_CHUNK_OVERLAP` | 1200, 150 | Size of each passage and the overlap between neighbours. Changing them means *Re-index*. |
| `RAG_EMBED_BATCH` | 16 | Passages per embedding request. |
| `RAG_TOP_K` | 5 | Results returned by default (1 to 10). |
| `RAG_MIN_SIMILARITY` | 0.30 | Meaning-matches below this are dropped. |
| `RAG_MAX_DOCUMENT_BYTES` | 5 MB | Largest document. |
| `RAG_MAX_CHUNKS` | 100000 | Most passages in the whole base. |
| `RAG_CACHE_MB` | 512 | Memory for fast search (0: read the database each time). |
| `RAG_SCAN_MINUTES` | 0 | Re-scan the knowledge folder every N minutes (0: only on request). |
| `RAG_FORMATS` | text, pdf, office, image | Formats accepted. |
| `RAG_VISION_ROUTE` | vision | The route whose model reads images and scanned pages. |
| `RAG_VISION_DAILY_LIMIT` | 200 | Images and scanned pages sent to the vision model per day (0: vision off). |
| `RAG_MAX_IMAGE_BYTES` | 10 MB | Largest image. |
| `RAG_MAX_PAGES` | 500 | Pages read from one PDF or presentation. |
| `RAG_OCR_MIN_CHARS` | 40 | A PDF page with fewer characters is treated as a scan. |
| `RAG_RENDER_MAX_SIDE` | 1600 | Longest side in pixels of a scanned page sent to the vision model. |
| `RAG_PARSE_TIMEOUT`, `RAG_PARSE_MEMORY_MB` | 60, 768 | Limits of the sandboxed process that opens each file. |

### review
| Setting | Default | Meaning |
|---|---|---|
| `REVIEW_ENABLED` | on | After some conversations the assistant reviews them in the background and saves what is worth keeping. |
| `REVIEW_EVERY_TURNS` | 5 | Fewest turns between reviews. |
| `REVIEW_MAX_INPUT_TOKENS` | 6000 | Cost cap of one review. |
| `WRITE_APPROVAL` | off | `off`, `memory`, `skills` or `all`: which saves wait for your approval in [Journey](#/guide?s=journey). |
| `NOTIFICATIONS` | on | `off`, `on` or `verbose`: the small messages about saves and fallbacks. |

### system
| Setting | Default | Meaning |
|---|---|---|
| `LOG_LEVEL` | INFO | How much the server writes to the [Logs](#/guide?s=logs). |

## Good to know

- Settings are stored in the database, not in `.env`. The `.env` file only carries the first administrator account and the image-level options (data directory, listen address).
- Moving settings between machines: the page keeps its own export and import through the API (`/api/config/export`), without secrets.
