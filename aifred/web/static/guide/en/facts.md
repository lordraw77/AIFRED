# Facts

Long-term memory. While you talk, the assistant picks out what is worth keeping (preferences, decisions, people, projects, outcomes) and recalls it in later conversations. The [Memory](#/guide?s=memory) files are two short notes sent with every request; facts are unlimited and are found again by search, so only the ones a request needs are added to it.

It is **off by default**. Turn it on with `AUTO_MEMORY` in [Config](#/guide?s=config) (group *memory*): `owner` keeps facts from your own chats, `all` also from other people's chats on the open Telegram bot and from the sub-agents you list in `MEMORY_AGENTS`.

## What you see

- **Status**: whether memory is on, how many facts there are, the model that reads conversations and the last time it ran.
- **The list**: each fact with its kind (*preference*, *decision*, *fact*, *outcome*), the names it mentions, when it was written and how often it was recalled. Filter by subject, kind and state, or search the text.
- **Subjects**: whose facts they are. `owner` is you; on the open Telegram bot every person has `user:<id>` (true of them everywhere) and `chat:<id>` (true of that conversation); a sub-agent has `agent:<name>`. A subject never sees another's facts, and nobody else sees the owner's.
- **Buttons**: *Edit*, *Archive* (hide it from recall), *Restore*, *Delete*, and *Add to notes* for a fact you use often, which copies it into `MEMORY.md`.

## How facts are made and found

- **Reading.** A conversation is read after `MEMORY_EVERY_TURNS` new turns, when you close it with `/new`, and when it has been idle for `MEMORY_IDLE_MINUTES`. Only the new messages are read, and tool output is left out.
- **Which model.** `MEMORY_EXTRACT_ROUTE`, empty by default, so the conversation's own route. The text of the conversation is sent to that model, so choose who receives it. If the route is unavailable and `MEMORY_EXTRACT_FALLBACK` is on, the conversation's own route is used, which already received the conversation.
- **Checks.** Every fact is checked in code before it is stored: length, injected instructions, hidden characters and anything that looks like a password or key are refused. A fact that repeats a known one is merged; one that corrects a known one replaces it (the old one stays, marked *superseded*, until it is deleted).
- **Recall.** For each message the best facts are added after it, in a block marked as untrusted notes: `MEMORY_RECALL_K` at most, `MEMORY_RECALL_TOKENS` long. They are ranked by relevance, recency and importance. With an embedding model (`MEMORY_EMBED_PROVIDER` and `MEMORY_EMBED_MODEL`, else the knowledge base's) meaning counts; without one, keywords do. The assistant can also search deeper with the `recall` tool.

## Keeping it tidy

- **Retention.** A fact not recalled for `MEMORY_RETENTION_DAYS` (180) is archived; an archived fact is deleted for good after `MEMORY_GRACE_DAYS` (30) more days, and until then *Restore* brings it back. Set retention to 0 to keep everything.
- **Pause.** *Pause* on a subject stops storing and recalling for it and keeps its facts. *Forget* deletes them all after a confirmation. Anyone can send `/forget` to the bot to delete what it remembers about them.
- **Export and import.** *Export* downloads facts as JSON (the format *Import* reads) or as Markdown, for everything or for one subject; vectors are not included. *Import* first shows what would happen (new facts, duplicates, refused rows) and changes nothing until you confirm; *merge* adds what is missing, *replace* first clears the subjects that are in the file. Imported facts go through the same checks as extracted ones.

## Good to know

- Facts are sent to the model as part of a request, so they follow the same rule as the notes: keep secrets out.
- A fact is data, never an instruction; it is shown to the model that way, and a stored text that looks like an injected instruction is not recalled.
- Scheduled tasks can use facts but never create them.
