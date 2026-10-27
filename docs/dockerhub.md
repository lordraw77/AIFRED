# AIFRED

![AIFRED(https://raw.githubusercontent.com/lordraw77/AIFRED/refs/heads/main/brand/lockup.svg)](https://raw.githubusercontent.com/lordraw77/AIFRED/refs/heads/main/brand/lockup.svg)
**A lean, self-hosted personal AI agent. Python, Docker, your models, your data.**

AIFRED is a small, from-scratch personal agent: persistent memory, skills that improve over time, tools (including MCP servers), a local knowledge base, and several ways to talk to it, all in **one Python process, one SQLite database and one container**. It runs on your hardware, uses the LLMs you choose (local or hosted) and is configured entirely from a web dashboard.

> **Status:** early release (`0.4.0`). It is tested (1,050+ automated tests) and the Telegram gateway, three MCP servers and a hosted vision model have been checked against real services, but expect rough edges and please report them.

## Links

| | |
| --- | --- |
| **This image** | <https://hub.docker.com/r/lordraw/aifred> |
| **Source code** | <https://github.com/lordraw77/AIFRED> |
| **Deployment guide** (OpenBao, backup, recovery) | <https://github.com/lordraw77/AIFRED/blob/main/docs/deploy.md> |
| **Specification** | <https://github.com/lordraw77/AIFRED/blob/main/docs/specs.md> |
| **Architecture decisions** | <https://github.com/lordraw77/AIFRED/blob/main/docs/adr.md> |
| **Changelog** | <https://github.com/lordraw77/AIFRED/blob/main/CHANGELOG.md> |
| **License** | [MIT](https://github.com/lordraw77/AIFRED/blob/main/LICENSE) |

## What you get

- **Gateways:** a web dashboard, a CLI, a Telegram bot (optional allowlist) and an OpenAI-compatible API (`/v1/chat/completions`).
- **Any LLM, set up from the browser:** Ollama and local OpenAI-compatible servers, Groq, OpenRouter, Google Gemini, Cloudflare Workers AI, Cerebras, Mistral, NVIDIA, Hugging Face, or any custom endpoint, with ordered routes, automatic fallback, key rotation, budgets and health.
- **Memory and skills:** small curated memory the agent maintains, skills that are saved and reused, and a background review that proposes what is worth keeping (with optional approval and undo).
- **Tools and MCP:** memory, search, web, files, optional terminal, and a built-in MCP client (Streamable HTTP, current and earlier revisions); a short note per server says what it is *for*, so the agent picks the right one.
- **Local knowledge base (RAG):** your documents (text, PDF, Office, OpenDocument, images and scans through a vision model you choose), hybrid semantic + keyword search with page citations. Files are parsed in a limited child process.
- **Safe by default:** single admin, scrypt passwords, session cookies with CSRF, hashed API keys, secrets encrypted at rest (key kept in OpenBao next to the container), non-root read-only container, no `pip` in the image, strict Content-Security-Policy on the dashboard.

## Quick start

Recommended: the full stack (AIFRED + OpenBao for the secrets key), from the repository:

```bash
git clone https://github.com/lordraw77/AIFRED.git && cd AIFRED
cp .env.example .env            # set AIFRED_ADMIN_USER and AIFRED_ADMIN_PASSWORD (12+ characters)
echo 'AIFRED_IMAGE=lordraw/aifred' >> .env
AIFRED_PULL=1 make docker/run   # pulls this image, starts OpenBao and AIFRED
# open http://127.0.0.1:8700
```

Just to try it (secrets key kept in the data volume, so not for production):

```bash
docker run -d --name aifred -p 127.0.0.1:8700:8700 -v aifred-data:/data \
  --read-only --tmpfs /tmp:size=64m,mode=1777 --cap-drop ALL --security-opt no-new-privileges:true \
  -e AIFRED_ADMIN_USER=admin -e AIFRED_ADMIN_PASSWORD='choose at least 12 characters' \
  lordraw/aifred:latest
```

Then open <http://127.0.0.1:8700>, sign in, and on **Models** add a provider (for example Ollama), press *Test* on a model and put it on the `default` route. The password variable is only read the first time; remove it afterwards.

## Tags and platforms

| Tag | Meaning |
| --- | --- |
| `0.4.0` | A specific version (pin this one for repeatable deployments) |
| `0.1.0`, `0.1.0.dev0` | Earlier builds |
| `latest` | The most recent build |

Platform: `linux/amd64`. An arm64 build is possible from the sources (see the deployment guide).

## Configuration

Almost everything is set on the dashboard and stored in the database. The container reads only:

| Variable | Purpose |
| --- | --- |
| `AIFRED_ADMIN_USER`, `AIFRED_ADMIN_PASSWORD` | The admin account, created on first start |
| `AIFRED_DATA_DIR` | Data directory (default `/data`) |
| `AIFRED_API_HOST` | Listen address (default `0.0.0.0` in the image; publish the port on `127.0.0.1` unless you put HTTPS in front) |
| `AIFRED_SECRETS_BACKEND` and `AIFRED_OPENBAO_*` | `file` (default) or `openbao` for the secrets key |

Port `8700`. State lives in the `/data` volume: database, memory, skills, workspace and knowledge files. Back it up together with the OpenBao volume (see the guide).

## Contributing and forking

AIFRED is open source under the **MIT License**: use it, change it, fork it, ship your own version. Issues and pull requests are welcome at <https://github.com/lordraw77/AIFRED>. To build the image yourself: `docker build -t aifred .` in the repository.

## License

[MIT](https://github.com/lordraw77/AIFRED/blob/main/LICENSE) © 2026 Alessandro Pioli.
