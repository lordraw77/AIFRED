# Memory

Two small files the assistant reads at the start of every conversation: what it knows, and what it knows about you. They are what lets it remember between conversations.

![The two memory files](/static/guide/img/memory-1.png)

## What you see

- **What I know (`MEMORY.md`)**: durable facts about your world: addresses, schedules, decisions. Default limit: 2,200 characters (`MEMORY_LIMIT`).
- **About you (`USER.md`)**: your name, preferences, how you like answers written. Default limit: 1,375 characters (`USER_LIMIT`).
- **The counter and the bar** of each file: characters used and the percentage of the limit. When a file is full the assistant has to remove something before saving more.
- **The graph** (below the two files): every name written between double brackets is a dot; two names in the same entry are joined by a line. Click a dot to see its entries.
- **The entries**: one per line, each with *Edit* and *Remove* (with a confirmation).
- **The box at the bottom** and *Add*: write an entry yourself.

## How to

- **Add a fact**: write it in the box, press *Add*. Short, factual sentences work best ("The router is at 10.70.0.1").
- **Correct one**: *Edit*, change the text, *Save*.
- **Let the assistant do it**: tell it "remember that…". It uses the `memory` tool; a toast says what was saved. It may also save things after a conversation (the background review, `REVIEW_ENABLED`).

## Names and the graph

![The graph under the memory files](/static/guide/img/memory-2.png)

- Write the people, hosts, projects and places an entry is about between double brackets: `[[Marco]] works at [[Acme]]`. Always spell a name the same way; the assistant is told to do so and to reuse names already in the notes.
- A dot's size is the number of entries that mention it; its colour says where they are (notes, profile or both). A line means two names share an entry; a thicker line, several.
- Entries written before this convention join the graph when they mention, in plain words, a name that other entries already bracket. Entries that mention no name are counted under the graph but are not drawn.
- The files remain the source of truth: the graph is drawn from them every time, so editing or removing an entry changes it at once.

## Size

- Both limits are settings: `MEMORY_LIMIT` for the notes and `USER_LIMIT` for the profile, between 300 and 50,000 characters ([Config](#/guide?s=config), group *agent*). The link under each counter opens Config.
- The whole content is sent with **every request**, so a larger file costs tokens each time. Raise a limit only when the bar is often full.
- Lowering a limit below what is already stored keeps the entries but refuses new ones until some are removed or merged.

## How the assistant picks facts

The background review reads what you said (your messages in full; its own long answers shortened when the conversation is long), compares it with the notes and the size of each file, and then adds, replaces or removes. It writes one fact per entry, with absolute dates and bracketed names, merges near-duplicates and tidies up when a file is above 85% full. `/refine` runs it by hand.

## Good to know

- **Approval.** With `WRITE_APPROVAL` set to `memory` or `all`, the assistant's saves wait in [Journey](#/guide?s=journey) until you approve them. Your own edits here are never held back.
- **Safety check.** Everything written to memory, by you or by the assistant, is scanned: text that looks like an injected instruction ("ignore the previous instructions…"), hidden characters and attempts to leak secrets are refused.
- **Undo.** Every change is in the Journey timeline with an *Undo* button.
- **It is sent to the model.** The memory is part of every request, so keep secrets out of it. The open Telegram bot never sees it.
