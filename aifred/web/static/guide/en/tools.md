# Tools

Which tools the assistant may use on each gateway, how often they were used, and what each group of tools is *for*.

![The toolset cards](/static/guide/img/tools-1-matrix.png)

## The toolset cards

- **One card per toolset**, with its name, the number of tools in it and its calls since the server started. Click the name to list its tools: each with its description, a badge `open bot ok` if it may be used by the open Telegram bot, and `not available now` when it needs something that is not set.
- **One switch per gateway** on the card: *CLI*, *WEB* (this dashboard's chat), *API*, *TELEGRAM* and *CRON* (scheduled tasks).
- **Click a switch** to turn a toolset on or off for that gateway. The lists on the [Config](#/guide?s=config) page are updated for you. A gateway that had no list of its own gets a copy of the shared one with your change, so the other gateways do not move.
- The calls on the right of each card count uses since the server started.

### The toolsets
`memory` (notes and profile), `skills`, `search` (search the past conversations) and `web` (web search and page fetch), `file` (read and write in the workspace), `terminal` (shell commands; off by default and handle with care), `ssh` (commands on your own hosts over SSH: `ssh_hosts`, `ssh_run`; off by default, see below), `knowledge` (your documents), `library` (answers you saved: `library_search`, `library_get`, `library_save`), `cron` (scheduled tasks), `agents` (delegate to sub-agents) and one `mcp:<server>` for each MCP server.

## Instructions: what is it for?

A tool's schema says *how* to call it, not *why* it exists. Here you write, for each MCP server and tool group, **when to use it and when not**. The assistant reads this in its prompt (the *Capabilities* section) and can ask for the full text with `tool_help`.

- **The counter** (*Capabilities section: 239 / 3000 characters*) shows how much of the budget `INSTRUCTIONS_BUDGET` is used. Over the budget the text is shortened automatically.
- **What the agent sees** opens the exact section.
- **Apply to open conversations** makes running conversations pick up your changes.
- **One row per group** with badges: `documented`, `undocumented` (an MCP server with nothing written), `not used` (written but turned off). *Edit* opens it.

### The editor

![Writing an instruction and testing it](/static/guide/img/tools-2-test.png)

- **The text** and its counter (about 600 characters is a good size), and *Use it in the prompt* to keep it without sending it.
- **Hint for the draft**: a few words ("my local Linux servers and Proxmox") to steer the model.
- **Generate** writes a draft from the tools the group offers and your other instructions; **Improve** rewrites the current text keeping what is right; **Shorten** cuts it. You see a draft and choose *Use this text* or *Discard*. Nothing is saved until you press *Save*.
- **Use my profile and notes for the draft** also sends your memory (names, address ranges) to the model that writes the draft; it is off by default.
- **Use my recent chats that missed this target** also sends up to six recent requests from your own chats (web, CLI, API; never an open Telegram bot) that look related to the group but were answered without it, so the draft covers what the assistant missed; off by default.
- **Test** dry-runs the model's choice: it writes about eight test requests (some that need the group, some near misses, some that need no tool), sends each to the model with the real tools, and reads only the first tool it picks. **No tool is executed.** You get a score and a pass/fail per request, and the score of the saved text for comparison.
- **Refine and test** lets the model rewrite the text up to three times from the failures and keeps the best-scoring one; you still decide whether to use it.
- **Save**, **Delete**, and **Notes for single tools** below for one-line notes about individual tools. An accepted text is recorded in [Journey](#/guide?s=journey) with its score (for example `[test 5/6]`).

## Good to know

- Tests need the group's tools to be on for *WEB*: if not, the page tells you to switch them on.
- At most 10 evaluations an hour (`ASSIST_EVALUATIONS_PER_HOUR`): each calls the model many times. The provider of the `assist` route (or `default`) receives the tool names and descriptions.
- In a chat, `/assist mcp:proxmox` does draft, test and score in one go.

## Remote terminal over SSH

The `ssh` toolset lets the assistant run a command on one of **your own hosts** (`ssh_hosts` lists them, `ssh_run` runs a command). It is off by default: add `ssh` to the toolsets of the gateways you trust, and fill in two settings.

- `SSH_HOSTS`: a JSON object, one entry per host, for example `{"pve1": {"user": "root", "host": "10.70.0.1", "port": 22, "host_key": "ssh-ed25519 AAAA…", "description": "Proxmox node"}}`. The assistant chooses a host by name and can never reach an address that is not listed.
- `SSH_PRIVATE_KEY`: an unencrypted private key, used for every host. Give it an account with only the rights the assistant needs.

`host_key` is mandatory: get it with `ssh-keyscan -t ed25519 10.70.0.1` (the part after the address). The connection is refused if the server presents another key, and nothing is learned on first use. The same destructive-command blocklist as the local terminal applies, plus a timeout (`SSH_TIMEOUT`) and an output cap. An open Telegram bot never gets the toolset.

## Questions from MCP servers

A server on the 2026-07-28 revision may stop a tool call to ask you something (a short form, for example "which project?"). The assistant then asks you in the chat and continues the call with `mcp_reply`. Forms for passwords, tokens or keys are always declined, `MCP_ELICITATION` turns the feature off, and `MCP_SAMPLING` (off by default) lets a server have the assistant's model write text for it. Such servers can also keep a stream open (`MCP_SUBSCRIPTIONS`) so a changed tool list is picked up at once.
