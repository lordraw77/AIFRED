# System

A quick look at the health of the server.

![The status of the server](/static/guide/img/system-1.png)

## What you see

- **Counters**: version, how long it has been running, conversations, messages and the size of the database.
- **Gateways**: the state of each connection to the outside.
  - **Telegram**: `running as @yourbot`, `starting`, `error` (with the reason) or `stopped`; a warning *open to everyone* if the allowlist is empty; and when the last message came.
  - **Knowledge base**: on or off, and how many passages it holds.
  - **MCP · name**: one line for each MCP server with its state, number of tools and protocol, or the error.
- **Storage**: where the data directory is, and the **secrets backend** (the encrypted local file, or OpenBao) with its state.

## Good to know

- The same Telegram and conversation numbers are at the bottom of the left menu, refreshed every 15 seconds. The *Restart* button there reconnects Telegram and the MCP servers without restarting the whole server.
- If the secrets backend is not green, tokens and keys cannot be read: providers, Telegram and OAuth servers will fail until it is back.
