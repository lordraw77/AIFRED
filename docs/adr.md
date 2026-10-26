# AIFRED — architecture decision records

## ADR-001: where the master key for stored secrets lives

**Date:** 25 October 2026. **Status:** accepted (OpenBao Transit), file backend kept as a fallback.

### Context

Provider API keys, the Telegram token and MCP headers are encrypted in the database. The first design kept the AES key in `data/.secret.key`, **on the same volume** as the database. That has two weaknesses: a backup or snapshot of the volume contains both the data and its key, and (with the terminal toolset on) a command running as the container user can simply `cat` the key. The requirement is to move the key out of the volume, next to the container.

### Options

| Option | For | Against |
| --- | --- | --- |
| A. Key file (status quo) | No extra service; works offline; nothing to unseal | Key and data travel together in every backup; readable by terminal commands |
| B. OpenBao, Transit engine | Purpose-built secrets manager: the key never leaves the server, access policies, audit log, rotation (`rewrap`), revocation of a client's access; same API as Vault | One more service to run, back up and unseal; secrets are unreadable while it is sealed |
| C. Valkey | Already in the lab; trivial to run | A Redis fork: an in-memory data store, not a secrets manager. No encryption at rest, no audit trail, no per-path policy of the same kind; AIFRED would need a password to reach it, which is again a secret to keep somewhere. It moves the file rather than solving the problem |

### Decision

**B**, using Transit rather than a key stored in OpenBao's KV engine. With KV, AIFRED would fetch the master key and hold it; with Transit AIFRED never holds a master key, only the decrypted values it is using. The file backend stays for development and for users without OpenBao; `AIFRED_SECRETS_BACKEND` selects one, and `python -m aifred admin migrate-secrets` moves between them.

Valkey is not rejected as software, only for this job. It becomes useful if AIFRED ever runs as several instances (shared rate limits, sessions, locks). Nothing needs it today.

### Consequences

- **Availability now depends on OpenBao being unsealed.** After a restart of the OpenBao container someone (or an auto-unseal mechanism) must unseal it before AIFRED can read any stored secret. AIFRED stays up and shows "sealed" in the dashboard, local providers that need no key keep working, and nothing is marked invalid because of the outage. The unseal strategy is an operator decision, to be settled with the compose files in phase 9: manual Shamir unseal (most secure, needs a person after every restart), static-key auto-unseal with the key on the host outside the AIFRED volume [VERIFY: static seal support in the OpenBao version used], or auto-unseal through a second OpenBao.
- **Hardening is part of the decision.** Moving the key is not enough if commands can lift the credentials from the service. `serve` therefore marks itself non-dumpable, which makes `/proc/<pid>/environ` and memory unreadable to same-user children (verified by a test that runs as an unprivileged user). Credentials must reach AIFRED as environment variables rather than `*_FILE` files when the terminal is enabled.
- **Residual risk, stated plainly:** anyone who controls the AIFRED process can ask OpenBao to decrypt; a terminal command that obtains the credentials another way can too. The full answer is to run commands in a separate container or user (already a roadmap item: remote terminal backend). OpenBao also gives an audit log and one-step revocation to limit the damage.
- **Operations:** back up OpenBao's storage and unseal keys separately from the `aifred-data` volume. A backup of only one of them is useless to an attacker, and equally to you.
- **Cost in code:** about 200 lines (`aifred/vault.py`, `aifred/hardening.py`), no new dependency (plain `httpx`), 30 offline tests against a fake server plus an optional suite against a real OpenBao.
- **Verified against a real server** on 25 October 2026, OpenBao 2.4.0 and again 2.7.1 (the version the deployment pins): AppRole login, Transit encrypt and decrypt, revoked token followed by automatic re-login, seal and unseal, wrong credentials, missing key, and a full stack run that left no key file and no plaintext in the database or WAL.
- **Deployment findings (see `docs/deploy.md`):** OpenBao 2.7 no longer has the `file` storage type (the deployment uses single-node Raft) and declares audit devices in the configuration file; since 2.5.3 the unauthenticated root-token endpoints are off by default, so after setup revokes the root token a break-glass procedure is needed (`scripts/openbao-root-token.sh`). The Docker `init: true` option was dropped: its process is PID 1, is dumpable, and holds the container's whole environment in `/proc/1/environ`, which would have exposed the AppRole credentials to terminal commands. AIFRED is now PID 1 itself, hardened, and reaps orphans (tested in a real PID namespace).

### Reference setup (to be scripted in phase 9)

```
bao secrets enable transit
bao write -f transit/keys/aifred type=aes256-gcm96            # not exportable by default
bao policy write aifred - <<'POLICY'
path "transit/encrypt/aifred" { capabilities = ["update"] }
path "transit/decrypt/aifred" { capabilities = ["update"] }
POLICY
bao auth enable approle
bao write auth/approle/role/aifred token_policies=aifred token_ttl=1h token_max_ttl=4h
```

Run the optional real-server tests with `AIFRED_TEST_OPENBAO_ADDR`, `AIFRED_TEST_OPENBAO_ROLE_ID`, `AIFRED_TEST_OPENBAO_SECRET_ID` (and `..._ROOT_TOKEN`, `..._UNSEAL_KEY` for the seal and revoke checks) set; `tests/test_openbao_live.py` is skipped otherwise.

---

## ADR-002: PostgreSQL next to (or instead of) SQLite

**Date:** 25 October 2026. **Status:** accepted: keep SQLite; revisit on the triggers below.

### Context

AIFRED is a personal agent with one admin. The database holds sessions, messages, a full-text index, settings, a journal and a few small tables: megabytes, written by at most two local processes (`serve` and `cli`).

### Options

| Option | For | Against |
| --- | --- | --- |
| A. SQLite in WAL mode (current) | Zero administration; one file to back up; FTS5 with accent folding already works; no network hop; fits the "one container" principle | One host; writers are serialised (irrelevant at this size); no remote access |
| B. PostgreSQL only | Real concurrency, replication, `pg_dump`, access from other machines | A second service and credentials to manage; FTS5 has to be rebuilt on `tsvector` plus `unaccent`/`pg_trgm`; a driver dependency (the specification limits dependencies); a rewrite and re-test of `store.py` and its migrations; slower startup ordering in compose |
| C. Both, behind a storage interface | Choice for the user | Two code paths to maintain and test; the lowest common denominator in SQL; the worst cost of both |

### Decision

**A.** Nothing in the current or planned scope needs what PostgreSQL offers, and its cost is a rewrite of the search layer plus another stateful service to secure and back up. Note that the earlier reason one might want an external database, keeping secrets away from the application volume, is already solved by ADR-001: the database now contains no key and no plaintext secret.

### Revisit when any of these becomes true

1. Several AIFRED instances must share state (high availability, or a worker per gateway).
2. Multi-user accounts with real concurrent write load (the specification keeps this out of scope).
3. The database grows beyond a few gigabytes of messages, or search latency becomes noticeable.
4. A requirement for remote or read-replica access, or for managed backups that SQLite files do not fit.

### How to keep the door open

`Store` is the only module that talks SQL, and the LLM tables live in a mixin. If a move is ever needed, the work is bounded: port `store.py` and `store_llm.py`, replace FTS5, and keep the migration list. No other module imports `sqlite3`. Valkey (see ADR-001) would join at that point for shared locks and rate limits.

---

## ADR-003: how the local knowledge base searches vectors

**Date:** 26 October 2026. **Status:** accepted on 26 October 2026 (specification 9.4).

### Context

The knowledge base stores a few thousand to a few tens of thousands of chunks, each with an embedding of 384 to 1536 floats, for a single user. The decision is how to compare a query vector with them. The specification asks for few dependencies, one container and one SQLite file.

### Options

| Option | For | Against |
| --- | --- | --- |
| A. Pure Python over `array`/`struct` | No dependency | About 38 million multiply-adds for 50,000 chunks at 768 dimensions: seconds per query in CPython, far too slow for a chat tool call |
| B. `numpy`, exact search over a float32 matrix | One well-maintained dependency; a single matrix product scores every chunk; exact, so results are reproducible and need no index to build or tune; trivial to test | Adds about 35 MB to the image; the searched collection's matrix is held in memory (3 MB per thousand 768-dimension chunks) |
| C. `sqlite-vec` extension | Vectors inside SQLite, approximate indexes | A native extension to build, ship and load in a read-only slim image; younger project; extra moving part for a corpus this small |
| D. A vector database container (Qdrant, Chroma, pgvector) | Scales further, filtering, persistence features | A second stateful service to secure, back up and keep unsealed or authenticated; contradicts the single-container principle; ADR-002's reasons against PostgreSQL apply |

### Decision

**B.** Exact search is both the simplest and fast enough at this scale (a matrix product over 50,000 × 768 float32 values is some tens of milliseconds with BLAS; this is an estimate to be measured in phase 3b). Retrieval is **hybrid**: the same chunks are also indexed with FTS5, and the two rankings are merged by reciprocal rank fusion. Keywords catch exact identifiers (hostnames, error codes, flags) that embeddings blur, embeddings catch paraphrases that share no word, and the keyword side keeps working when the embedding service is down.

### Consequences

- New dependency `numpy`, pinned and hash-locked like the others (`requirements.lock`); manylinux wheels exist for x86-64 and arm64, so the image still needs no compiler.
- Memory is the limit that matters, not time, so it is bounded and configurable: matrices of recently searched collections live in an LRU cache of `RAG_CACHE_MB` (default 512 MB), loaded by streaming rows into a preallocated array (peak: one matrix plus one batch); a collection that does not fit is scanned from the database in blocks with constant memory. `RAG_MAX_CHUNKS` defaults to 100,000 (about 307 MB at 768 dimensions) and the container limit rises from 512 MB to 1 GB by default (`AIFRED_MEM_LIMIT`). Vectors can be stored as `float16` later to halve all of this.
- Vectors live in the main database, so the knowledge base is covered by the existing backup and restore of `aifred-data`; it also makes the volume larger (see the sizing table in 9.4).
- Changing the embedding model invalidates a collection's vectors; the collection is re-embedded in the background and searched by keywords meanwhile.

### Revisit when

1. A collection needs more than about 500,000 chunks (memory, or search above 100 ms): move the matrix to `float16` or `int8`, then to `sqlite-vec` or an HNSW index.
2. Search must filter by metadata at scale, or several users need separate access rules.
3. Retrieval quality, measured on a set of golden questions, calls for reranking (roadmap) rather than a different index.

---

## ADR-004: reading PDF, Office documents and images for the knowledge base

**Date:** 26 October 2026. **Status:** proposed. It adds two dependencies, so it is to be confirmed before the second step of the knowledge base is implemented (specification 9.4).

### Context

The second step must ingest PDF, Office documents (docx, xlsx, pptx, odt, ods, odp) and images (jpg, png, webp). These are hostile-input formats: parsers for them are a classic source of vulnerabilities, and the service holds the OpenBao credentials. Images and scanned pages also need their text read, which classically means OCR.

### Options

| Option | For | Against |
| --- | --- | --- |
| A. Standard library only | No dependency | No PDF at all; images and scans unreadable |
| B. `pypdf` + `pypdfium2`, Office through `zipfile`/`xml.etree`, images and scans through the vision model, everything parsed in a sandboxed child process | Two small wheels with no system packages; Office formats need no dependency; the vision model reads photos, screenshots and scans better than classic OCR and reuses providers, failover and budgets already built; no native OCR engine to ship | `pypdfium2` bundles native PDFium code; vision reading costs tokens or needs a vision-capable local model; transcription can be wrong |
| C. System tools in the image (poppler, tesseract, LibreOffice) | Mature, local OCR | A large image, many native packages to patch, a much bigger attack surface; LibreOffice in a read-only hardened container is awkward |
| D. Document-AI libraries (unstructured, docling) | Layout-aware extraction | Very large dependency trees (machine-learning runtimes), slow, hard to hash-lock and keep small |

### Decision

**B.** Text PDFs use `pypdf` (pure Python); pages with no text layer are rendered with `pypdfium2` and sent, like any image, to a vision model through a dedicated route (`RAG_VISION_ROUTE`). Office files are read with the standard library after defensive checks. All parsing runs in a child process that has no credentials and is limited in time, CPU and memory, so a parser bug costs one document and not the service. If `pypdfium2` is rejected, scanned PDFs are reported as "needs OCR" and everything else still works.

### Consequences

- Two new dependencies, pinned and hash-locked like the rest. Wheel sizes and platform availability are `[VERIFY]` before implementation (the image must stay buildable without a compiler on x86-64 and arm64).
- The vision route has no default provider: images (metadata stripped first) go only where the admin sends them, and a paid provider must sit last in the route. A daily limit protects the budget.
- Machine-read text is approximate and labelled as such; it is also scanned for injected instructions after reading.
- The sandbox is only as strong as process isolation inside the container (no credentials, rlimits, minimal environment). It does not stop a parser exploit from reading `/data`; hence the single container is still the boundary, and the toolset stays admin-only.

### Revisit when

1. A local OCR engine becomes preferable to a vision model (cost, latency, accuracy on dense scans): add tesseract behind the same interface.
2. Layout matters (columns, forms, tables in scanned pages): consider a layout-aware parser in a separate container.
3. Parser vulnerabilities in `pypdfium2` or `pypdf` make a separate parsing container worthwhile.
