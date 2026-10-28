# AIFRED — Docker deployment

## What you get

```
                host (127.0.0.1:8700)
                        │ published
 ┌──────────────────────┴──────────────┐         ┌───────────────────────────┐
 │ aifred   uid 10001, read-only, no   │  vault  │ openbao   uid 100, read-only, no
 │          capabilities, 1 GB         ├─────────┤           capabilities, 256 MB
 │          internet: yes              │ network │           internet: no, no published port
 └───────────────┬─────────────────────┘ (internal)└──────────────┬────────────┘
                 │ aifred-data (/data)                            │ openbao-data, openbao-logs
```

- **aifred**: the agent (API now; dashboard and gateways as the phases land; the local knowledge base, text formats, is built). All state is in the `aifred-data` volume. It holds no master key: the key for stored secrets is a Transit key inside OpenBao.
- **openbao**: the secrets server (ADR-001 in [adr.md](adr.md)). Reachable only from the AIFRED container, over an internal Docker network that has no route to the outside and publishes no port.
- **searxng** (optional, profile `search`): web search for the `web_search` tool, reachable by AIFRED at `http://searxng:8080`.

## Requirements

Docker with the Compose plugin (v2), about 1 GB of free memory, and for local models a reachable Ollama or vLLM (from the container: `http://host.docker.internal:11434`, or a LAN address). Keep swap disabled or encrypted on the host: OpenBao runs without `mlock`.

## First run

```bash
git clone <repository> aifred && cd aifred      # or copy the project directory
make docker/run                                  # same as scripts/setup.sh; safe to repeat
```

The script creates `.env` (mode 0600), starts OpenBao, initialises it, unseals it, creates the Transit key, a policy that allows only encrypt and decrypt on that key, and an AppRole for AIFRED, **revokes the root token**, builds and starts AIFRED, and finally removes the bootstrap admin password from `.env` and recreates the container without it. At the end it prints the URL and, once, the admin password.

Two files appear next to `.env`, both ignored by git:

| File | What it is | What to do |
| --- | --- | --- |
| `openbao-unseal.key` | The key that unseals OpenBao | Copy it into your password manager. Keeping it on this machine is convenient (`scripts/unseal.sh` uses it) but it then sits next to the AppRole credentials. Delete it if you prefer to paste the key after each restart |
| `.env` | AppRole credentials of AIFRED, port and tags | Mode 0600; do not commit |

With `OPENBAO_KEY_SHARES=5 OPENBAO_KEY_THRESHOLD=3 make docker/run` the key is split in five shares, three of which unseal (`openbao-unseal.key` then holds all five lines; spread them out).

## Day to day

| Task | Command |
| --- | --- |
| Logs | `make logs` |
| Stop (data kept) / start | `make docker/stop` / `make docker/run` |
| **After OpenBao restarts** (host reboot, `docker compose restart`) | `make unseal`. Until then AIFRED runs but cannot read or write stored secrets; the dashboard shows "sealed" (`GET /api/system/vault`), nothing is marked invalid, and models that need no API key keep working |
| Web search | `docker compose --profile search up -d`, then set `SEARXNG_URL` to `http://searxng:8080` on the Config page |
| Reset the admin password | `docker compose exec aifred python -m aifred admin reset-password` |
| Change port or interface | set `AIFRED_PORT` / `AIFRED_BIND` in `.env`, then `docker compose up -d` (use `0.0.0.0` only behind something that terminates TLS) |
| Update AIFRED | pull the new code, `make test`, `make docker/run` (rebuilds and recreates `aifred`; data is kept) |
| Update OpenBao | change `OPENBAO_VERSION` in `.env`, run the real-server tests (below), `docker compose up -d openbao`, `make unseal` |
| Move secrets in from the file backend | `docker compose exec aifred python -m aifred admin migrate-secrets` (the old key must still be in the volume) |

## Using AIFRED

- **Command line:** `docker compose exec aifred python -m aifred cli` (add `--session NAME` to resume, `-m "text"` for one question). Type `/help` for the commands.
- **Telegram:** create a bot with @BotFather, then set `TELEGRAM_TOKEN` in the dashboard. With `TELEGRAM_ALLOWED_IDS` empty the bot is **open to everyone** with a restricted set of capabilities; message it from your account to learn your numeric id (it answers strangers with it) and put that id in the list to get the full agent. Changes apply without a restart.
- **OpenAI-compatible API:** `POST /v1/chat/completions` with an API key of role `chat`; the `model` field selects a route or `provider/model`. Pass `X-AIFRED-Session` to keep a conversation.

## MCP servers

Add servers on the dashboard (MCP page, phase 6) or with `PUT /api/mcp/servers/<name>`; the connection is tested before saving. Header values (tokens) are encrypted in the secret store and never shown again. To seed a fresh install put a `mcp.json` in the data volume (format in the specs, 9.2): it is imported once, its headers are encrypted and the file is deleted. Then enable the tools for a gateway by adding `mcp` (all servers) or `mcp:<name>` to `TOOLSETS`. `/mcp` shows the state of every server and `/mcp reload` reconnects. MCP servers are reached over HTTP(S) only, so run each one as its own container on a network the AIFRED container can reach; for a certificate from a private CA mount the PEM file and set `verify_tls` to its path.

## Using the published image

The image is on Docker Hub as [`lordraw/aifred`](https://hub.docker.com/r/lordraw/aifred) (tags `0.5.0` and `latest`, linux/amd64; `0.4.1`, `0.4.0`, `0.3.0`, `0.2.1`, `0.2.0`, `0.1.1`, `0.1.0` and `0.1.0.dev0` are earlier builds). To run the stack from it instead of building:

```bash
echo 'AIFRED_IMAGE=lordraw/aifred' >> .env     # and AIFRED_VERSION=0.5.0 to pin a version
AIFRED_PULL=1 make docker/run                  # pulls the image instead of building it
```

or, by hand, `docker compose pull aifred && docker compose up -d`. The image holds only the code (no `.env`, keys or data), runs as an unprivileged user and has no `pip`; it is checked with Trivy before each push (no HIGH or CRITICAL finding that has a fix). To publish a new version: `make lint test`, then `docker build --build-arg VERSION=<version> -t lordraw/aifred:<version> -t lordraw/aifred:latest .` and `docker push` both tags. An arm64 image needs QEMU on the build host (`docker run --privileged --rm tonistiigi/binfmt --install arm64`, then `docker buildx build --platform linux/amd64,linux/arm64 --push ...`); the dependencies already ship arm64 wheels.

## Knowledge base (RAG)

Off by default. To use it:

1. **Pick an embedding model** on a provider you have added (Models page, or `POST /api/providers/{id}/models/{model}/embedding-test`). A local model (Ollama, vLLM) keeps your documents on the machine; a hosted one receives every chunk. Then set `RAG_ENABLED`, `RAG_EMBED_PROVIDER` and `RAG_EMBED_MODEL` on the Config page. Without a model, documents are still indexed for keyword search.
2. **Add documents**, any of these ways:
   ```bash
   # drop files in a sub-folder named after the collection, then ask for a scan
   docker compose exec aifred mkdir -p /data/workspace/knowledge/homelab
   docker compose cp ./notes/. aifred:/data/workspace/knowledge/homelab/
   curl -s -b cookies -H "X-CSRF-Token: $CSRF" -X POST http://127.0.0.1:8700/api/rag/scan -H 'content-type: application/json' -d '{}'

   # or upload one file (the file is the raw request body)
   curl -s -b cookies -H "X-CSRF-Token: $CSRF" -X POST --data-binary @runbook.md \
     "http://127.0.0.1:8700/api/rag/documents?collection=homelab&name=runbook.md"
   ```
   With `RAG_SCAN_MINUTES` above 0 the folder is rescanned by itself. Text formats only for now: Markdown, text, HTML, JSON, CSV, YAML and source code.
3. **PDF, Office files and images (optional):** they are read in a separate limited process. Images and scanned pages need a model that can read images: on the Models page add the provider, press *Test vision* on the model, then put it on the **vision** route (a local model such as `qwen3.5:4b` first keeps images on your machine; a hosted one, for example on NVIDIA, goes where you choose; put any paid provider last). Until the route has a model nothing is sent anywhere and such pages are marked partial. `RAG_VISION_DAILY_LIMIT` (200) caps the images sent per day. Reading an image takes 20 to 80 s on a hosted free tier and 3 to 4 minutes on a CPU-only Ollama (a full page at the default 1,600 px can take much longer): use a hosted model first and the local one as a fallback, or lower `RAG_RENDER_MAX_SIDE` for slow local models. The vision calls and *Test vision* wait at least 300 s for the first token unless the route entry sets its own timeouts.
4. **Let the model use it**: add `knowledge` to `TOOLSETS` (or to one gateway's list). It is never offered to an open Telegram bot.

Things to know:

- **Memory.** Searching keeps a collection's vectors in memory (`RAG_CACHE_MB`, 512 by default; 3 MB per thousand 768-dimension chunks). A collection that does not fit is read from the database in blocks, slower but bounded. The container limit is `AIFRED_MEM_LIMIT` (1 GB by default): raise it together with `RAG_CACHE_MB` and `RAG_MAX_CHUNKS` if you index a lot.
- **Disk and backups.** Expect about 55 MB per 10,000 chunks at 768 dimensions (an estimate). Documents live in the `aifred-data` volume, so `make backup` covers them, in clear text like conversations.
- **Changing the embedding model** makes the vectors of the collection stale. Searches keep working (keywords only for the stale part) while you run a re-index (`POST /api/rag/reindex`), which embeds everything again; on a paid provider that costs tokens.
- **A document that stays `waiting`** means the embedding service is down or its token budget is reached; it is retried by itself with growing delays. `error` documents show the reason; fix the cause, then re-index or upload again.

## Backup and restore

```bash
make backup                                   # ./backups/aifred-data-*.tar.gz and openbao-data-*.tar.gz
scripts/backup.sh aifred ./elsewhere          # only one of them, other directory
scripts/restore.sh aifred backups/aifred-data-20261025T180344Z.tar.gz
scripts/restore.sh openbao backups/openbao-data-20261025T180344Z.tar.gz
```

- The two archives are separate **on purpose**: one without the other cannot be used to read the stored secrets. Keep them in different places, and keep `openbao-unseal.key` apart from both.
- Each service is stopped while its volume is copied (a few seconds of downtime); OpenBao comes back sealed and is unsealed again when `openbao-unseal.key` is present.
- A restore replaces the volume. After restoring OpenBao, run `make unseal` if the script could not.
- The OpenBao audit log (`/openbao/logs/audit.log`, volume `openbao-logs`) is not rotated; truncate it from time to time. If its disk is full OpenBao refuses requests.

## Recovery

| Situation | What to do |
| --- | --- |
| Lost the **unseal key** | OpenBao cannot be unsealed and its data is unrecoverable, so the stored secrets are lost with it. Remove the `openbao-data` volume, run `make docker/run` again to create a fresh OpenBao, and re-enter the API keys and the Telegram token in the dashboard. Nothing else is affected |
| Lost `.env` (AppRole credentials) | Get a root token (next row), then `bao write -f -field=secret_id auth/approle/role/aifred/secret-id` and `bao read -field=role_id auth/approle/role/aifred/role-id`, and put both in `.env` |
| Need a **root token** (none exists after setup) | Follow the header of `scripts/openbao-root-token.sh`: enable the break-glass listener setting, restart, unseal, run the script, use the token, revoke it, disable the setting |
| Lost the admin password | `docker compose exec aifred python -m aifred admin reset-password` |
| OpenBao refuses requests | Check the audit log volume is not full: `docker compose exec openbao df -h /openbao/logs` |

## Security notes

- **No key on the AIFRED volume.** A copy of `aifred-data` contains ciphertext only.
- **AIFRED is PID 1** in its container (no `init: true`). `serve` marks itself non-dumpable, so a command run through the terminal tool, which has the same user id, cannot read `/proc/1/environ` (which holds the AppRole credentials) or the service's memory. A separate init process would have been readable. AIFRED reaps the orphans of terminal commands itself.
- **Credentials as environment variables, not files.** A mounted file would be readable by those commands. They are visible to `docker inspect`, i.e. to whoever controls Docker on the host, who can anyway read everything.
- **Least privilege.** Both containers run read-only, with all capabilities dropped and `no-new-privileges`; `aifred` has a 256-process limit; AIFRED's policy can only `update` `transit/encrypt/aifred` and `transit/decrypt/aifred`; the Transit key is not exportable.
- **No TLS inside the Docker network.** AIFRED and OpenBao talk plain HTTP over a private network with no other members. For TLS, mount a certificate into OpenBao (see the comments in `deploy/openbao/config.hcl`) and set `AIFRED_OPENBAO_ADDR=https://…` and `AIFRED_OPENBAO_CACERT`.
- **The dashboard is HTTP on 127.0.0.1 by default.** To expose it, put a reverse proxy with TLS in front; the session cookie is `Secure` when the request arrives over HTTPS.
- **Residual risk:** whoever controls the AIFRED process can ask OpenBao to decrypt. Do not enable the terminal toolset for gateways you do not fully trust (see the specification, section 9).

## Environment reference

Read by **docker compose** and the scripts from `.env` (or the shell; the shell wins):

| Variable | Default | Meaning |
| --- | --- | --- |
| `AIFRED_ADMIN_USER` | `admin` | Admin user name; used when no admin exists |
| `AIFRED_ADMIN_PASSWORD` | generated | Bootstrap password (min. 12 characters); cleared by `setup.sh` after the first start |
| `AIFRED_OPENBAO_ROLE_ID`, `AIFRED_OPENBAO_SECRET_ID` | written by `setup.sh` | AppRole credentials |
| `AIFRED_OPENBAO_ADDR`, `AIFRED_OPENBAO_MOUNT`, `AIFRED_OPENBAO_KEY` | `http://openbao:8200`, `transit`, `aifred` | Where and how AIFRED reaches OpenBao |
| `AIFRED_BIND`, `AIFRED_PORT` | `127.0.0.1`, `8700` | Interface and port published on the host |
| `AIFRED_MEM_LIMIT` | `1g` | Memory limit of the AIFRED container. The knowledge base caches vectors in memory (`RAG_CACHE_MB`, 512 MB by default; 307 MB for 100,000 chunks of 768 dimensions) and the document parsers run inside the same limit. Raise this together with `RAG_CACHE_MB` and `RAG_MAX_CHUNKS` |
| `AIFRED_VERSION` | `0.5.0` | Image tag |
| `OPENBAO_VERSION`, `SEARXNG_VERSION` | `2.7.1`, `2026.10.24-ef0ec03da` | Image tags of the other services |
| `SEARXNG_SECRET` | generated | SearXNG server secret (profile `search`) |
| `AIFRED_SETUP_ADMIN_CREATED` | set by `setup.sh` | Marker that the admin exists; do not edit |
| `COMPOSE_PROJECT_NAME` | `aifred` | Run several stacks side by side |

Read only by the **scripts**, from the shell: `OPENBAO_KEY_SHARES` (1), `OPENBAO_KEY_THRESHOLD` (1), `KEEP_ADMIN_PASSWORD=1` (keep the bootstrap password in `.env`), `OPENBAO_UNSEAL_KEY_FILE` (`./openbao-unseal.key`), `OPENBAO_ROOT_TOKEN_FILE` (`./openbao-root.token`, only during setup), `COMPOSE` (`docker compose`), `COMPOSE_PROGRESS` (`quiet`).

Passed into the **container**: exactly the `AIFRED_*` variables listed in `docker-compose.yml` (plus `AIFRED_SECRETS_BACKEND=openbao`, fixed there). Anything else in `.env` stays on the host. `AIFRED_DATA_DIR=/data` and `AIFRED_API_HOST=0.0.0.0` are set by the image. Every other setting lives in the database and is edited from the dashboard.

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `setup.sh` stops with "timed out waiting for OpenBao" | `docker compose logs openbao`. A config error shows there; fix `deploy/openbao/config.hcl` and run again |
| AIFRED healthy but "secret store unavailable" in answers | OpenBao is sealed: `make unseal` |
| `setup.sh` says OpenBao is initialised but AIFRED has no credentials | The first run stopped after initialising and `.env` was lost; see Recovery |
| Port 8700 already in use | set `AIFRED_PORT` in `.env`, then `docker compose up -d` |
| Cannot reach Ollama on the host | use `http://host.docker.internal:11434` as the provider URL, and make Ollama listen on more than localhost |
| Image build fails on a hash mismatch | `requirements.lock` is out of date: `make lock` |

## Scripts

| Script | Purpose |
| --- | --- |
| `scripts/setup.sh` | First run; also repairs a half-finished one. Safe to repeat |
| `scripts/unseal.sh` | Unseal OpenBao (key file, or prompt) |
| `scripts/backup.sh`, `scripts/restore.sh` | Volume archives |
| `scripts/openbao-root-token.sh` | Break-glass root token |
| `scripts/release.sh` | Prints the release commands; runs none |

## Verifying a deployment against a real OpenBao

`tests/test_openbao_live.py` runs the secrets backend against a real server (see ADR-001). Because the OpenBao network is internal, run it from a container on that network, for example:

```bash
docker create --name aifred-live --entrypoint sh --user 10001 -e HOME=/tmp --tmpfs /tmp:size=128m,mode=1777 \
  -e AIFRED_TEST_OPENBAO_ADDR=http://openbao:8200 -e AIFRED_TEST_OPENBAO_ROLE_ID -e AIFRED_TEST_OPENBAO_SECRET_ID \
  -v "$PWD/tests:/t/tests:ro" -v "$PWD/aifred:/t/aifred:ro" -w /t aifred:0.5.0 \
  -c 'pip install -q --target /tmp/pt pytest==9.1.1 && PYTHONPATH=/t:/tmp/pt python -m pytest -q -p no:cacheprovider tests/test_openbao_live.py'
docker network connect aifred_vault aifred-live && docker start -a aifred-live; docker rm aifred-live
```

(The seal and revoke checks also need `AIFRED_TEST_OPENBAO_ROOT_TOKEN` and `AIFRED_TEST_OPENBAO_UNSEAL_KEY`.)
