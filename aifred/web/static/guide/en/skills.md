# Skills

A skill is a saved procedure: a short Markdown document the assistant can load when a task matches it ("how the nightly backup works", "write the weekly report of the servers"). Only the one-line description is in the assistant's prompt; the full text is read when needed, so skills cost almost nothing until they are used.

![The skill editor with the preview open](/static/guide/img/skills-1-editor.png)

## What you see

- **New skill** (top left) and the **list of skills**: name in bold, description under it. Click one to open it.
- **The editor** on the right:
  - **Name**: lowercase letters, digits, `-` and `_`. It cannot be changed after the skill is saved.
  - **Description**: one line saying *when the assistant should use it*. This is what it reads to choose. Under the field, **Generate** and **Improve** ask the model to write or rewrite the description from the skill's text; you see a draft and choose *Use this text* or *Discard*.
  - **Edit / Preview**: write the body in Markdown, or see how it renders.
  - **Save** and **Delete** (with a confirmation).

## How to

1. *New skill*, give it a name.
2. Write the description as a trigger: "Use when the user asks how to restore a file from the NAS backup".
3. Write the steps in the body, as you would explain them to a colleague.
4. *Save*. The assistant sees it from its next conversation (or press *Apply to open conversations* on the [Tools](#/guide?s=tools) page).

## Good to know

- **The assistant can write skills too.** After solving something complex it may save a skill (tool `skill_save`); with `WRITE_APPROVAL` on, that waits in [Journey](#/guide?s=journey). It can never delete a skill: only you can.
- **Skills are files** in the `skills` folder of the data directory, so they are included in your backups.
- **Sub-agents** can have skills preloaded into their prompt: see [Agents](#/guide?s=agents).
- **Safety.** Skill text is scanned like memory: injected instructions and hidden characters are refused.
