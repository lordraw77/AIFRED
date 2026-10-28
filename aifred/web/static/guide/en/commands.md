# Chat commands and Telegram

A message that starts with `/` is a command, not a question for the model. Commands work the same in the web chat, on Telegram and in the command line.

![The answer to /help](/static/guide/img/commands-1-help.png)

## The commands

| Command | What it does |
|---|---|
| `/start` | A greeting. |
| `/help` | Lists the commands. |
| `/new` | Starts a new conversation (on Telegram too). |
| `/sessions` | Lists your recent conversations. |
| `/think on\|off\|auto` | Turns the model's reasoning mode on or off for this conversation. |
| `/model [route\|provider/model\|auto]` | Shows or chooses the model route of this conversation. |
| `/kb [scan\|search <query>]` | Knowledge base: re-read the knowledge folder, or search it. |
| `/pending` | Lists what waits for your approval. |
| `/approve <id>` / `/reject <id>` | Approves or discards a waiting item. |
| `/mcp [reload]` | Shows the MCP servers and their state; `reload` reconnects them. |
| `/refine` | Asks the assistant to review the conversation now and save what is worth keeping. |
| `/journey [delete <id>]` | Shows what the assistant learned; undoes an entry. |
| `/instructions [reload\|<name>]` | Shows what each tool group is for. |
| `/assist <scope> [hint]` | Drafts or improves the instruction of a tool group (for example `/assist mcp:proxmox my cluster`), tests it and shows the score. It saves nothing. |
| `/cron [list\|add\|pause\|resume\|run\|remove]` | Manages scheduled tasks (see below). |
| `/agent <name> <task>` | Hands a task to a sub-agent. |
| `/agents` | Lists the sub-agents you can use. |
| `/quit` | Leaves the command-line chat (command line only). |

### Scheduling from a command
```
/cron add morning | every morning at 8 | Summarise the news about Python
/cron add report | ogni lunedì alle 9 | Write the weekly report of the servers
/cron list
/cron pause morning
/cron run morning
```
The second part may be a sentence in English or Italian, five cron fields, an alias such as `@daily` or `every 30m`. See [Schedules](#/guide?s=schedules).

## Telegram

1. Create a bot with @BotFather and copy its token.
2. In [Config](#/guide?s=config) set `TELEGRAM_TOKEN` (it is write-only: once saved it is never shown again).
3. Write to the bot. If your id is not on the allowlist it answers with your numeric id.
4. Put that id in `TELEGRAM_ALLOWED_IDS` (several ids are separated by commas). The change applies at once.
5. The sidebar and [System](#/guide?s=system) show `running as @yourbot` when it works.

### What to know about the bot
- It answers in **private chats only**; groups and channels are ignored.
- Answers are sent formatted (bold, italic, code, links, lists). If Telegram refuses the formatting the message is sent again as plain text, so nothing is lost. Long answers are split into several messages.
- With an **empty allowlist the bot is open to everyone**. It then runs in a restricted mode: no memory or profile, no knowledge base, no MCP servers, no terminal, a rate limit, and only the commands `/start`, `/help`, `/new` and `/think`. The sidebar warns *open to everyone*. Prefer an allowlist.
- Scheduled tasks deliver their result to chats on the allowlist only.
- Each Telegram chat has its own conversation; `/new` starts another.
