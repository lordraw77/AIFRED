# Logs

What the AIFRED server is doing, live: connections, tool calls, errors, scheduled runs, Telegram, MCP. Useful when something does not work.

![The live log](/static/guide/img/logs-1.png)

## What you see

- **Level**: `DEBUG`, `INFO`, `WARNING` or `ERROR`. Lines at that level and above are shown (the choice is remembered). `INFO` is the default.
- **Pause / Resume**: stops new lines from being added to the view (the server keeps logging).
- **Follow**: keeps the view at the newest line; untick it to read in peace.
- **Clear view**: empties the view only.
- **The lines**: time, level, the part of AIFRED that wrote it, and the message. The last 500 lines are kept in the view and new ones arrive live.

## Good to know

- API keys, tokens and passwords are never written to the log; error messages are cleaned of them.
- The level the server records is `LOG_LEVEL` in [Config](#/guide?s=config); this page only filters what is shown.
- If the live updates stop (for example you were signed out) a warning line says so.
