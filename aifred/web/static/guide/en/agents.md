# Agents

A sub-agent is a specialist the main assistant can hand a task to: a researcher for the web, a sysadmin for your servers, a writer for texts. Each has its own instructions, model, tools and limits, works on the task alone, and returns an answer.

![The list of sub-agents](/static/guide/img/agents-1-list.png)

## What you see

- **Let the main assistant delegate (toolset agents)**: switches that turn the `agents` toolset on for each gateway. Without it the assistant has no `delegate` tool.
- **Sub-agents** and **New agent**. Three starters are created the first time, *off*: `researcher` (web), `sysadmin` (your servers, read-only first) and `writer` (texts).
- **One card per agent**: dot (green = on), name, badges for its model route and memory mode, then *Turn on/off*, *Test*, *History*, *Edit*, *Delete*. Under it: what it is for, its toolsets and skills, its limits.
- **Test**: write a task and press *Run*; you see the answer, the tokens used, the model and the conversation it ran in.
- **History**: the latest runs with status, time, tokens, the task and the output.
- **Save to library** under a successful result (in *Test* and in *History*): keeps it in the [Library](#/guide?s=library).

## The form

![The form of a new agent](/static/guide/img/agents-2-form.png)

| Field | Meaning |
|---|---|
| **Name** | Lowercase letters, digits, `-` and `_`. |
| **What it is for** | One line the main assistant reads to decide when to hand a task over. *Generate* and *Improve* help write it. |
| **Role** | The agent's instructions. It replaces the main persona. The agent sees nothing of your conversation, only the task it is given. *Generate*, *Improve* and *Shorten* help. |
| **Model route** | Which route answers for this agent: use a cheaper model for simple jobs. |
| **Toolsets** | Comma-separated: `web`, `search`, `file`, `mcp:linux_ssh`… The agent gets only what both it and the calling gateway have. |
| **Skills** | Skills preloaded into its prompt. |
| **Memory** | `none` (no access), `read` (sees your notes and profile) or `write` (may also save; `WRITE_APPROVAL` applies). |
| **Tool rounds / Time limit / Token budget** | Limits for one run; empty uses the defaults (`AGENT_*` settings). |

## How to

- **Use one in a chat**: switch the `agents` toolset on for *Web*, turn an agent on, then ask: "ask the researcher to look for…". Or type `/agent researcher <task>`.
- **Make your own**: *New agent*, give it a clear *What it is for* and a *Role*, choose the smallest set of toolsets it needs, press *Test*.

## Good to know

- **An agent never has more power than the caller.** Its toolsets are the intersection of its own and the gateway's; it can never use `agents`, `cron` or the background review, so it cannot delegate again or schedule tasks.
- **Not for the open Telegram bot**: delegation is refused in open chats.
- **Everything is recorded.** Each run is a child conversation (source `sub:<name>`), linked to the one that delegated, with its tokens counted separately in [Models](#/guide?s=models) usage.
- Several tasks can run in parallel (`delegate_many`, up to `AGENT_MAX_PARALLEL`, 3). Limits: `AGENT_MAX_ITERATIONS` (8), `AGENT_TIMEOUT` (300 s), `AGENT_MAX_AGENTS` (20).
