# Getting started

AIFRED is a personal assistant that runs on your own hardware. You talk to it from the dashboard, from Telegram or from any app that speaks the OpenAI API. It can remember things, read your documents, call tools on your servers, run tasks on a schedule and hand work to specialist sub-agents. This guide walks through every page of the dashboard, one section at a time.

![The sign-in page](/static/guide/img/start-1-login.png)

## Signing in

- The user name is the one set in `AIFRED_ADMIN_USER` (default `admin`).
- On the very first start the password is the one in `AIFRED_ADMIN_PASSWORD` in the `.env` file. After the first start the account exists in the database: remove the password from `.env`, and change it on the [Account](#/guide?s=account) page.
- Too many wrong attempts lock the sign-in for a while; the message tells you how long.

## The first ten minutes

1. **Add a provider.** Open [Models](#/guide?s=models), press *Add provider*, pick a preset (a hosted service, or *Ollama* for a model on your own machine), paste the API key and press *Test and save*.
2. **Test a model.** Open the provider's *Models* list and press *Test* on one model. It must answer a tool-calling request before AIFRED will use it.
3. **Put it on the default route.** In *Routes*, add the tested model to `default` and press *Save order*. A route is an ordered list of models: if the first fails, the next one answers.
4. **Say hello.** Open [Chat](#/guide?s=chat) and write a message.
5. **Optional: Telegram.** In [Config](#/guide?s=config), set `TELEGRAM_TOKEN` (from @BotFather) and `TELEGRAM_ALLOWED_IDS` (your numeric Telegram id; write to the bot once and it tells you the id). See [Chat commands and Telegram](#/guide?s=commands).

## The words you will meet

| Word | Meaning |
|---|---|
| **Provider** | A service that runs models: a hosted API or a local engine such as Ollama. |
| **Model** | One model of a provider. It must pass a tool-calling test before use. |
| **Route** | An ordered list of models with automatic fallback. `default` is used by chat; `vision`, `compress` and others are optional. |
| **Gateway** | A way of talking to AIFRED: the web chat, Telegram, the API, the command line, and scheduled tasks. |
| **Toolset** | A group of tools (`memory`, `web`, `file`, `agents`, `cron`, `mcp:<server>`…). Each gateway has its own list of enabled toolsets. |
| **Memory** | Short notes the assistant keeps between conversations (`MEMORY.md`) and about you (`USER.md`). |
| **Library** | Answers and results you chose to keep (a recipe, a checklist); the assistant reads them back when a request matches. |
| **Skill** | A saved procedure the assistant can load when a task matches. |
| **MCP server** | An external program that offers more tools (for example your Proxmox or Linux hosts). |
| **Connector** | A ready-made MCP server of a product (Gmail first) with a guided set-up and sign-in; see [Connectors](#/guide?s=connectors). |
| **Approval** | With `WRITE_APPROVAL` on, what the assistant wants to save or schedule waits for your yes in [Journey](#/guide?s=journey). |

## Moving around

- The left menu has one entry per page. The small *Guide* button at the top right of every page opens the part of this guide about it.
- Keyboard: press `g` then `c`, `s`, `m`, `k` or `j` to jump to Chat, Sessions, Memory, Skills or Journey; `/` moves to the search or filter box; `Esc` closes the menu on a phone.
- At the bottom of the menu: the state of Telegram, the number of conversations, *Restart* (restarts the Telegram poller and reconnects the MCP servers), the theme button (auto, teal, sand) and *Sign out*. The arrow at the top collapses the menu.
- Everything works on a phone: the menu becomes a drawer opened with the button at the top left.

## Where the data lives

Everything is in the data directory (`/data` in the Docker image): the database, the memory files, the skills, the knowledge folder and the encrypted secrets. Back that folder up. API keys of providers are stored encrypted and are never shown again.

Next: [Chat](#/guide?s=chat).
