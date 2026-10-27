# Security policy

AIFRED holds API keys, can read your documents and, if you enable them, run commands and call other servers, so security reports are taken seriously.

## Reporting a vulnerability

**Please do not open a public issue for a security problem.** Report it privately through GitHub: on the repository, open **Security → Report a vulnerability** ([private vulnerability reporting](https://github.com/lordraw77/AIFRED/security/advisories/new)). If that is not available to you, open an issue that only says you have a security report and ask for a private channel, without details.

Please include what you found, how to reproduce it (a minimal request, file or configuration), the version (`System` page, or the image tag) and what an attacker gains. Do not include real secrets.

You can expect an acknowledgement within a few days and a fix or a clear decision after that. This is a small project maintained in spare time, so times are not guaranteed; please give a reasonable period before disclosing publicly, and you will be credited in the changelog if you wish.

## Supported versions

| Version | Supported |
| --- | --- |
| the latest release (`latest` image, newest tag) | yes |
| older releases | no: please upgrade |

## What AIFRED defends, and what it does not

The design and the threats considered are in [docs/specs.md](docs/specs.md) (section 11) and [docs/adr.md](docs/adr.md). In short:

- **Defended:** unauthorised access to the dashboard and API (single admin, scrypt, session cookies with CSRF, hashed API keys, login rate limiting, loopback by default); secrets at rest (encrypted; the key lives in OpenBao next to the container, not on the data volume); server-side request forgery from `web_fetch` and URL ingestion; path traversal in file tools; prompt injection through web pages, files, MCP servers and indexed documents (tool output is declared untrusted, writes to memory and skills pass a scanner and an optional approval queue, the agent cannot write to the knowledge base); what an open Telegram bot may do (no memory, no files, no terminal, no MCP, no knowledge base); hostile PDF, Office and image files (parsed in a limited child process, archive and XML defences, metadata stripped before images leave the machine); XSS on the dashboard (strict Content-Security-Policy, no HTML built from data).
- **Not defended, by design, and documented:**
  - The **terminal** toolset is opt-in and guarded by a blocklist, not a sandbox; it runs as the container user and can read `/data`.
  - The **parser sandbox** limits time, CPU and memory and removes credentials from the child, but it does not stop a parser exploit from reading files the service user can read: the container is the real boundary.
  - The dashboard speaks **plain HTTP**. Publish the port only on `127.0.0.1`, or put a reverse proxy with HTTPS in front of it, before exposing it to a network.
  - The **prompt-injection scanner is a heuristic**; text sent to a hosted LLM leaves your machine, and so do the images and document text you send to a hosted vision or embedding provider (the dashboard names who receives what).
  - There is a single administrator; there are no per-user permissions.

## Running it safely

- Keep the data volume and the OpenBao volume private and back them up together (see [docs/deploy.md](docs/deploy.md)); losing the OpenBao unseal key makes the stored secrets unrecoverable.
- Remove `AIFRED_ADMIN_PASSWORD` from `.env` after the first start, and use API keys of scope `chat` for clients that only chat.
- Enable the terminal, file tools and MCP servers only on the gateways that need them, and leave the Telegram allowlist set unless you really want an open bot.
- Prefer a local vision and embedding model for private documents; put paid or external providers last in a route.
- Update the image regularly; releases are scanned with Trivy before they are published.
