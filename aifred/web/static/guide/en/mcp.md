# MCP

MCP (Model Context Protocol) is a standard way for programs to offer tools to an assistant. An MCP server can give AIFRED the tools to look at your Proxmox cluster, run commands on your Linux hosts, control Home Assistant, and so on. Each server becomes a toolset named `mcp:<name>`.

![The list of MCP servers](/static/guide/img/mcp-1-servers.png)

## What you see

- **Servers**, with **Reload** (reconnect all) and **Add server**.
- **One card per server**:
  - the dot and the state badge: `connected`, `connecting`, `error` or `disabled`, the protocol it speaks and warnings (*TLS not verified*);
  - **Test**: connects now and reports the protocol, the number of tools and the time it took. **Edit**, **Delete** (with a confirmation).
  - the address (or `command:` for a program) and *N tools exposed · latency · call timeout*;
  - **Use in**: switches that turn this server's tools on or off for *CLI*, *Web*, *API*, *Telegram* and *Scheduled*, or *On everywhere*. The lists on [Config](#/guide?s=config) are updated for you. A gateway without its own list gets a copy of the shared one, so others do not move. (If a gateway uses the whole `mcp` group you must remove that on Config to pick servers one by one.)
  - **What is this for?**: the instruction the assistant reads to know when to use this server (see [Tools](#/guide?s=tools)), with *Edit*;
  - **Dropped tools** (a tool the server described badly is left out, with the reason) and the *only these / never these* filters;
  - for OAuth servers, the sign-in row (below).

## Adding a server

![The form of a new server](/static/guide/img/mcp-2-form.png)

| Field | Meaning |
|---|---|
| **Name** | `a-z`, `0-9` and `_`, up to 24 characters. It becomes the toolset `mcp:<name>` and part of each tool's name. |
| **URL** | The server's address, `http://` or `https://`. Credentials do not go in the URL: use a header. |
| **or Command** | Instead of a URL: a program AIFRED starts (see *Stdio* below). |
| **TLS** | Verify the certificate, do not verify (self-signed; a warning stays on the card), or use your own CA file. |
| **Call timeout / Connect timeout** | Seconds; empty uses `MCP_CALL_TIMEOUT` (120) and `MCP_CONNECT_TIMEOUT` (15). |
| **Tool cap** | The most tools taken from this server (default `MCP_MAX_TOOLS_PER_SERVER`, 40). |
| **Protocol** | `auto` (recommended: tries the newest revision, then the earlier ones) or one to force. |
| **Only these tools / Never these tools** | Comma-separated filters. |
| **What is this for?** | Required for a new server: say when the assistant should use it and when not. *Generate* helps once the server is saved. |
| **Headers** | For example `Authorization: Bearer …`. Values are stored encrypted and never shown again; leave a value empty on edit to keep it. |
| **Enabled** / **Save without testing** | Enabled servers connect; saving normally tests the connection first. |
| **Sign in with OAuth** | See below. |

Press **Test and save**.

## Programs (stdio)

Instead of a URL, a server can be a **command** that AIFRED starts and talks to through its standard input and output, for example `uvx some-mcp-server`. It is powerful, so it is **off until you list the program** in `MCP_STDIO_COMMANDS` on Config (by file name). The program gets a minimal environment (none of AIFRED's variables or secrets; only the variables you set for it, in the *Headers* area, stored encrypted), its own private folder, and is stopped together with anything it started. It runs inside the AIFRED container with its privileges, and the standard image contains only Python, so install what you need first.

## OAuth

Some servers want you to sign in with a provider in the browser. Tick **Sign in with OAuth**, save, and press **Authorize** on the card: the provider opens in a new tab, you accept, and AIFRED stores the tokens encrypted and refreshes them. The card shows *OAuth: signed in*; **Forget the sign-in** removes the tokens.

You need a client identity: either a client id registered by hand with the provider (`MCP_OAUTH_CLIENT_ID`), or, if AIFRED can be reached from the internet over https, its public address in `MCP_OAUTH_PUBLIC_URL` (AIFRED then serves the client metadata document the provider fetches). A home network that is not reachable from outside needs the first.

## Good to know

- Servers reconnect by themselves: an unreachable one is retried after 5, 10, 20, 40 and 60 seconds and never blocks anything. Tools are re-read regularly; if a tool's definition changes it is recorded in [Journey](#/guide?s=journey).
- Tool descriptions and results from a server are **untrusted data**: the assistant never follows instructions found in them.
- The open Telegram bot never has MCP tools.
- A server that asks for more input in the middle of a call (sampling, elicitation) is not supported: the model is told it asked.
