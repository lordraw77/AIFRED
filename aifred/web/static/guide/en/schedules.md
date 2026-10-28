# Schedules

A scheduled task is a prompt the assistant runs by itself at the times you choose, with the result delivered to Telegram. "Every morning at 6, search the news about Python and send me five bullet points."

![The list of scheduled tasks](/static/guide/img/schedules-1-list.png)

## What you see

- **Scheduled tasks** and **New task**. A notice appears if the scheduler is off (`SCHEDULER_ENABLED`) or its clock is not running.
- **Let the assistant list, run, pause and propose tasks (toolset cron)**: the switches turn the `cron` toolset on or off for each gateway, so you can say "schedule…" in a chat. The assistant can list, run, pause and resume tasks at once; **creating, changing or removing a task waits for your approval** in [Journey](#/guide?s=journey).
- **One card per task**: a coloured dot (green = on), the name, the schedule as it is stored, and the buttons *Run now*, *Pause* / *Resume*, *History*, *Edit*, *Delete*. Under it the prompt and a line: next run, last result and when, number of runs, and where it is delivered. *proposed by the model* marks tasks the assistant suggested.
- **History** lists the latest runs (the last 50 are kept): status (`ok`; `error`, which includes running out of time; `missed`), when, and the output or the error.

## Writing the schedule

The **When** field understands plain sentences in **English and Italian**, besides cron.

![A sentence in Italian understood as cron](/static/guide/img/schedules-2-sentence.png)

While you type, a line under the field says *Understood as `30 8 * * 1-5`* and lists the next five run times, so you can check it before saving. The task is stored as that cron expression.

![A sentence in English](/static/guide/img/schedules-3-english.png)

| You write | Stored as |
|---|---|
| `every morning at 6` · `ogni mattina alle 6` | `0 6 * * *` |
| `every evening at 8` · `ogni sera alle 8` | `0 20 * * *` |
| `every day at 6:30 pm` · `tutti i giorni alle 18:30` | `30 18 * * *` |
| `every night at 2` · `ogni notte alle 2` | `0 2 * * *` |
| `every day at 8am and 6pm` | `0 8,18 * * *` |
| `ogni giorno alle 8 e alle 20` | `0 8,20 * * *` |
| `every day at noon` · `ogni giorno a mezzogiorno` | `0 12 * * *` |
| `ogni giorno alle 6 e mezza` | `30 6 * * *` |
| `weekdays at 7` · `nei giorni feriali alle 7` | `0 7 * * 1-5` |
| `from monday to friday at 8:30` · `dal lunedì al venerdì alle 8:30` | `30 8 * * 1-5` |
| `every monday and thursday at 9am` · `ogni lunedì e giovedì alle 9` | `0 9 * * 1,4` |
| `every weekend at 10` · `nel weekend alle 10` | `0 10 * * 0,6` |
| `every 2 hours` · `ogni 2 ore` | `0 */2 * * *` |
| `every 30 minutes` · `ogni 30 minuti` | `*/30 * * * *` |
| `every hour` · `ogni ora` | `0 * * * *` |
| `the 1st of every month at 8` · `il primo del mese alle 8` | `0 8 1 * *` |
| `il 15 del mese alle 9` | `0 9 15 * *` |
| `every month on the 15th at 10` | `0 10 15 * *` |

Other forms still work: five cron fields (`0 8 * * 1-5`: minute, hour, day of the month, month, day of the week), aliases (`@hourly`, `@daily`, `@weekly`, `@monthly`) and `every 30m` / `every 2h` / `every 1d`.

**If something is missing the form says what.** "Every morning" alone is refused with *at what time?*; "every week" asks *which day of the week?*; "the last day of the month" cannot be written in cron and suggests a day up to 28; "every 2 hours at 8" mixes an interval with a time; several times of the day must share the same minutes (8:00 and 20:00, not 8:00 and 20:30: make two tasks). "Morning" never means a time by itself: AIFRED does not guess. Evening and afternoon add 12 hours to a number below 12 ("ogni sera alle 8" is 20:00), night does the same from 6 to 11 ("alle 11 di notte" is 23:00).

## How to

1. *New task*. Name: lowercase letters, digits, `-` and `_`.
2. **When**: a sentence, e.g. `ogni mattina alle 6`. Check the *Understood as* line.
3. **What to do**: a complete instruction. Nobody can answer questions while it runs, so say everything it needs.
4. **Deliver the result to**: a Telegram chat from your allowlist, or *Do not deliver* (the result stays in History).
5. *Save*, then *Run now* to try it.

## Good to know

- **Time zone.** Times use `SCHEDULER_TIMEZONE` (for example `Europe/Rome`; empty means the server's zone). Summer-time changes are handled.
- **Not more often than every 5 minutes** (`SCHEDULER_MIN_INTERVAL`), at most 20 tasks (`SCHEDULER_MAX_TASKS`), one run at most 10 minutes (`SCHEDULER_RUN_TIMEOUT`), two at a time (`SCHEDULER_MAX_PARALLEL`).
- **Unattended runs are limited on purpose.** They use the route `ROUTE_CRON` and only the toolsets in `TOOLSETS_CRON` (by default search, web and skills; change it with the *Scheduled* column of the [Tools](#/guide?s=tools) page). They cannot create other tasks and are not reviewed for memory.
- **Each slot runs once.** A run still going when the next is due is skipped (it is only written in the Logs); a slot missed by more than 15 minutes (the server was off) is recorded as `missed`, not run. A run cut short by a restart is shown as an error.
- **Delivery is limited to chats on `TELEGRAM_ALLOWED_IDS`**, checked when you save and again at every send.
- You can also use `/cron` in any chat ([Chat commands](#/guide?s=commands)).
