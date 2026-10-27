# Contributing to AIFRED

Thank you for looking. AIFRED is open source under the [MIT License](LICENSE): you can use it, change it and fork it freely, and contributions are welcome, from a typo to a new feature. By contributing you agree that your work is released under the same license.

## Ways to help

- **Report a bug** or **ask for a feature** in [GitHub issues](https://github.com/lordraw77/AIFRED/issues). For a bug, say what you did, what you expected, what happened, your AIFRED version (`System` page on the dashboard) and the relevant lines of the **Logs** page. Remove anything private first.
- **Report a vulnerability** privately, not in an issue: see [SECURITY.md](SECURITY.md).
- **Try it with a service the project has not met** (a provider, an MCP server, a Telegram setup, an unusual document) and tell us what broke. Several integrations are tested mostly against fakes, so this is the most valuable kind of report.
- **Improve the docs**, or send a fix or a feature as a pull request.

## Before a larger change

Open an issue or a draft pull request first, so the idea can be discussed before you spend time on it. AIFRED is deliberately small: *one Python process, one SQLite database, one container*. A change is easier to accept when it keeps that, adds no service and, ideally, no dependency (new dependencies need an architecture decision record in [docs/adr.md](docs/adr.md)). [docs/specs.md](docs/specs.md) says how each part is meant to behave and [docs/analysis.md](docs/analysis.md) records what is done and what is open.

## Set up

Requires Python 3.13 and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/lordraw77/AIFRED.git && cd AIFRED
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -e '.[dev]'

.venv/bin/ruff check .        # lint (must be clean)
.venv/bin/pytest              # the suite takes a few minutes
```

Run it locally with a throw-away data directory:

```bash
AIFRED_DATA_DIR=./data AIFRED_ADMIN_USER=admin AIFRED_ADMIN_PASSWORD='at least 12 chars' \
  .venv/bin/python -m aifred serve        # http://127.0.0.1:8700
```

## Pull requests

1. Branch from `main`; keep a pull request to one topic.
2. **Tests come with the change.** The suite uses fake servers on real sockets (LLM, MCP, Telegram, OpenBao in `tests/`), so a feature that talks to the outside can be tested without credentials. For security-relevant code (authentication, secrets, file and URL handling, the parser sandbox, what an open gateway may do) check that your test really fails when the protection is removed: break the code on purpose and run the test.
3. `ruff check .` and `pytest` pass.
4. **Update the docs that the change touches:** the specification for behaviour, [CHANGELOG.md](CHANGELOG.md) for anything a user would notice, [docs/deploy.md](docs/deploy.md) for deployment, [docs/analysis.md](docs/analysis.md) for status.
5. Say in the description what changed, why, and how you checked it.

## Style

- Match the code around yours: naming, comment density and idiom. Comments say *why*, not what; public functions have short docstrings.
- Text meant for people is plain and short. User-visible strings are English.
- The dashboard is plain HTML, CSS and ES modules with **no build step and no external resource**. Never build HTML from data: use `textContent` and DOM construction (a test enforces it), and keep scripts and styles in files (the Content-Security-Policy forbids inline ones).
- Secrets: never log them, never return them from an API, never put them in a test fixture that looks real.

## Security-sensitive areas

Changes to these get extra scrutiny and need a test that fails without the change: `vault.py` and the secrets backends, `auth.py`, the tool registry and the `restricted` rules for open gateways, `tools/web.py` (SSRF), file tools, `rag_parse.py` and `rag_sandbox.py`, the scanner, and the Content-Security-Policy.

## Building and publishing the image

`docker build --build-arg VERSION=<version> -t aifred .` builds locally. Maintainers publish to Docker Hub (`lordraw/aifred`); the steps are in [docs/deploy.md](docs/deploy.md) and `scripts/release.sh`.
