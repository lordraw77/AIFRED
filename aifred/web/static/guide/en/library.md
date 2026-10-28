# Library

The place for answers and results you want to keep: a recipe, a plan, a checklist, a report an agent wrote. Save it once; later, when you ask for it in the chat or an agent needs it, the assistant reads the saved text from here instead of writing it again.

![Saving an answer from the chat](/static/guide/img/library-1-save.png)

## What you see

- **Items and the counter** (`3 / 500 items`): every saved text, latest change first. The limit is `LIBRARY_MAX_ITEMS`.
- **Use in**: switches that turn the `library` toolset on for each gateway (CLI, Web, API, Telegram, Scheduled). Without it the assistant cannot read or save library items there. The open Telegram bot never has it.
- **The search box**: finds items by words in the title, the text and the tags; accents do not matter. **The tag buttons** under it filter the list; press one again to remove the filter.
- **One row per item**: the title, its tags, where it came from (*from a chat*, *from an agent*, *written here*) and when it last changed, with the first words below. Click the row to open it.
- **An open item** shows the text formatted, then the buttons *Copy*, *Edit*, *Regenerate*, *Restore previous* (only after a change) and *Delete* (with a confirmation), and the request that produced it.
- **New item**: write one by hand (title, tags, the request, the text).

![The library with an item open](/static/guide/img/library-2-list.png)

## How to

### Keep an answer from the chat
Under every answer of the assistant there is **Save to library**. Press it: the title is filled in from the answer (change it if you like), add tags if you want, press *Save*. The request you made is stored with the text so it can be regenerated. If the title already exists, the button becomes *Replace it*: the old text is kept for one undo.

You can also ask in words: "save this recipe as *Fried rice*". The assistant uses the tool `library_save`. It saves only when you ask.

### Keep the result of an agent
When the assistant hands a task to a sub-agent, the answer appears as a tool line in the chat; open it and press **Save to library**. On the [Agents](#/guide?s=agents) page the same button sits under the result of *Test* and under each run in *History*.

### Get it back
Just ask: "give me the fried rice recipe". The system prompt lists the titles of the latest items, and the assistant searches (`library_search`) and reads the best match (`library_get`), then gives you the saved text as it is. It works in the chat, in Telegram (for you, not for the open bot), in the API and for sub-agents that have the `library` toolset.

### Write it again
Open an item and press **Regenerate**. AIFRED asks again with the stored request, in a throw-away conversation that uses the same model and tools as the web chat, and shows the new text under the old one as a draft. *Use this version* replaces the text (the old one is kept: *Restore previous* brings it back, and pressing it again goes forward); *Discard* throws the draft away. Nothing changes until you press *Use this version*. An item without a stored request cannot be regenerated: press *Edit* and write the request in the field *Request*.

![A regenerated draft next to the saved text](/static/guide/img/library-3-regenerate.png)

### Edit by hand
*Edit* opens the title, tags, request and text. Saving a changed text keeps the old one for one undo.

## Settings

| Setting | What it does |
|---|---|
| `LIBRARY_ITEM_CHARS` | Largest item (20,000 characters by default). |
| `LIBRARY_MAX_ITEMS` | Most items (500 by default). |
| `LIBRARY_PROMPT_ITEMS` | How many of the latest titles are listed in the system prompt (30 by default; 0 lists none, the assistant can still search). |

## Good to know

- **It is the user's text, not instructions.** What the assistant reads from the library is treated like any tool result: it is data. A save made by the assistant is scanned like a memory entry and refused if it looks like an injected instruction.
- **Approval.** With `WRITE_APPROVAL` set to `all`, the assistant's saves wait in [Journey](#/guide?s=journey). What you save with the buttons here is never held back.
- **Not the same as Memory or Knowledge.** [Memory](#/guide?s=memory) is a few short facts sent with every request; the [Knowledge](#/guide?s=knowledge) base is your documents, searched by passages; the library is whole texts you chose to keep, read only when a request matches.
- **Size.** Only the titles of the latest items go into the prompt; the texts are read on demand, so a big library costs nothing until it is used.
