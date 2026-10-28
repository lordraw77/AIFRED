# Memory

Two small files the assistant reads at the start of every conversation: what it knows, and what it knows about you. They are what lets it remember between conversations.

![The two memory files](/static/guide/img/memory-1.png)

## What you see

- **What I know (`MEMORY.md`)**: durable facts about your world: addresses, schedules, decisions. Limit: 2,200 characters.
- **About you (`USER.md`)**: your name, preferences, how you like answers written. Limit: 1,375 characters.
- **The counter and the bar** of each file: characters used and the percentage of the limit. When a file is full the assistant has to remove something before saving more.
- **The entries**: one per line, each with *Edit* and *Remove* (with a confirmation).
- **The box at the bottom** and *Add*: write an entry yourself.

## How to

- **Add a fact**: write it in the box, press *Add*. Short, factual sentences work best ("The router is at 10.70.0.1").
- **Correct one**: *Edit*, change the text, *Save*.
- **Let the assistant do it**: tell it "remember that…". It uses the `memory` tool; a toast says what was saved. It may also save things after a conversation (the background review, `REVIEW_ENABLED`).

## Good to know

- **Approval.** With `WRITE_APPROVAL` set to `memory` or `all`, the assistant's saves wait in [Journey](#/guide?s=journey) until you approve them. Your own edits here are never held back.
- **Safety check.** Everything written to memory, by you or by the assistant, is scanned: text that looks like an injected instruction ("ignore the previous instructions…"), hidden characters and attempts to leak secrets are refused.
- **Undo.** Every change is in the Journey timeline with an *Undo* button.
- **It is sent to the model.** The memory is part of every request, so keep secrets out of it. The open Telegram bot never sees it.
