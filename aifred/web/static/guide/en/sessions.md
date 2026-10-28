# Sessions

Every conversation AIFRED has, from every gateway, in one place: the web chat, Telegram, the API, scheduled tasks and sub-agents.

![The overview of the sessions](/static/guide/img/sessions-1-overview.png)

## What you see

- **The four counters** at the top: total conversations, total messages, how many sources (web, telegram, cron, api, sub-agents) and how many platforms are up.
- **The source filter**: shows only the conversations of one source (`web`, `telegram`, `cron`, `sub:researcher`…).
- **Overview / history**: two views of the same page. The choice is remembered.

### Overview
- **Connected platforms**: `api_server` (the HTTP API and this dashboard) and `telegram` with its state and when it last received something.
- **Recent sessions**: title, source, number of messages, when it was last updated and a preview of the last words. Click a row to open it in [Chat](#/guide?s=chat) (read-only unless it is a web conversation).
- **The pencil on each row** opens a small menu: a text box and *Save title* to rename, *Export* to download the conversation as a Markdown file, *Delete* (with a confirmation).

### History
Search the full text of every conversation: type in the box and press `Enter` (the `/` key jumps to the box). Each result shows the conversation, the source, who spoke and when, with your words highlighted. Click a result to open that conversation.

![Searching the history](/static/guide/img/sessions-2-history.png)

## How to

- **Find something you said weeks ago**: History, a distinctive word, `Enter`.
- **Keep a conversation outside AIFRED**: pencil, *Export*.
- **Tidy up**: pencil, *Delete*. Deleting a conversation also deletes the sub-agent conversations it started. Messages are removed for good.

## Good to know

- Conversations started by a scheduled task have the source `cron` and the title *Scheduled: name*. Sub-agent runs have the source `sub:<agent>` and are linked to the conversation that delegated.
- Long conversations are summarised for the model (see [Chat](#/guide?s=chat)) but the complete record is kept here.
