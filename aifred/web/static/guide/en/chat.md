# Chat

The chat is the main way to use AIFRED from the browser. Answers stream in as they are written, tools run in the middle of a reply, and everything is saved as a conversation.

![A conversation with a tool call and a table in the answer](/static/guide/img/chat-1-conversation.png)

## What you see

- **New** (top left): starts an empty conversation. The previous one stays in the list.
- **The conversation list**: the web conversations, newest first (up to 50), with the first words of the first message as title and the number of messages. Click one to open it. Move the mouse over a row to show the trash can; pressing it asks *Delete / No*, so a stray click removes nothing. Deleting also removes the sub-agent conversations that conversation started.
- **The messages**: *You* on top, *AIFRED* below. The assistant's text is formatted: headings, lists, tables, quotes, code, bold and italic, and links that open in a new tab. HTML is never interpreted.
- **Tool lines** such as `mcp__proxmox__list_vms()`: each time the assistant uses a tool a line appears. Click it to open the result, click again to close it. The marker ▸ means closed, ▾ open.
- **Save to library** under each answer: keeps it with the request that produced it (see [Library](#/guide?s=library)).
- **The usage line** under an answer: which model answered and how many tokens the request used.
- **The box at the bottom**: `Enter` sends, `Shift+Enter` adds a line. Typing `/` starts a command (see [Chat commands and Telegram](#/guide?s=commands)). While an answer is being written the *Send* button becomes *Stop*.

![A tool result opened](/static/guide/img/chat-2-tool-result.png)

## How to

### Ask for something that needs a tool
Just ask in plain words: "list the VMs of the Proxmox cluster". The assistant decides which tool to call. If it never uses a tool you expected, check that the toolset is on for the web chat in [Tools](#/guide?s=tools) and that its instruction says when to use it.

### Hand a task to a sub-agent
Say it: "ask the researcher to look for the latest news about the Roma". The assistant uses the `delegate` tool, which appears as a tool line; its answer is collapsed so a long report does not bury the conversation. Open it to read the full answer. This needs the `agents` toolset and an enabled agent ([Agents](#/guide?s=agents)).

![The answer of a sub-agent, collapsed and opened](/static/guide/img/chat-3-agent-answer.png)

### Read a conversation from another place
Conversations that started on Telegram, in a scheduled task or in a sub-agent can be opened from [Sessions](#/guide?s=sessions). They open read-only: the box explains that you can start a new conversation here instead.

## Good to know

- **Long conversations are summarised.** When a conversation grows past about 12,000 tokens the oldest messages are condensed into notes (`COMPRESS_*` settings) and a notice says so. Nothing is deleted: the page still shows every message.
- **Notices.** Small messages appear as toasts: a memory entry saved, a model fallback, a summary made. `NOTIFICATIONS` in [Config](#/guide?s=config) turns them off or makes them verbose.
- **Approvals.** With `WRITE_APPROVAL` on, memory and skill saves do not happen at once: they wait in [Journey](#/guide?s=journey).
- **The model.** The web chat uses the route `ROUTE_WEB` (default `default`). `/model` changes it for one conversation.
- **Privacy.** Everything you write goes to the provider of the route, as with any hosted model. Use a local provider for sensitive material.
