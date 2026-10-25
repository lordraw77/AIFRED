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
