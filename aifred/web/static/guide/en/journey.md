# Journey

The place where you stay in control of what the assistant changes by itself: what waits for your approval, and a timeline of everything that was learned or changed, with undo.

![Approvals and timeline](/static/guide/img/journey-1.png)

## Waiting for approval

When `WRITE_APPROVAL` in [Config](#/guide?s=config) is `memory`, `skills` or `all`, the assistant does not save at once: its proposals wait here. Each shows a number, the time, the conversation it came from and what it wants to do:

- a **memory** entry to add, change or remove;
- a **skill** to create or change;
- an **instruction** for a tool group;
- a **scheduled task** to create, change or remove, with its schedule and where the result would go (task proposals always wait, whatever the setting).

Press **Approve** to apply it or **Reject** to discard it. Text is screened again when you approve. In a chat you can also use `/pending`, `/approve <id>` and `/reject <id>`.

## Timeline

Newest first: the time, a badge for the kind (`memory`, `skill`, `instruction`, `settings`…), the action, a summary and a status:

- `applied` (done), `pending`, `approved`, `rejected`, `blocked` (the safety check refused it; the reason is shown), `undone`.
- **Undo** appears on applied memory, skill and instruction changes: it puts the previous text back.

Things you do yourself also appear: settings changed, MCP servers added, instructions saved with the score of their test.

## Good to know

- The safety check blocks text that looks like an injected instruction, hidden characters and attempts to leak secrets, both when the assistant proposes and again when you approve.
- Turning approval on is a good idea while you get to know the assistant; turn it off when you trust it.
