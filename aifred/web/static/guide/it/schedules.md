# Schedules

Un'attività pianificata è un prompt che l'assistente esegue da solo agli orari che scegli, con il risultato consegnato su Telegram. "Ogni mattina alle 6 cerca le notizie su Python e mandami cinque punti."

![L'elenco delle attività pianificate](/static/guide/img/schedules-1-list.png)

## Cosa vedi

- **Scheduled tasks** e **New task**. Compare un avviso se lo scheduler è spento (`SCHEDULER_ENABLED`) o il suo orologio non gira.
- **Let the assistant list, run, pause and propose tasks (toolset cron)**: gli interruttori attivano o disattivano il toolset `cron` per ogni gateway, così puoi dire "pianifica…" in una chat. L'assistente può subito elencare, eseguire, mettere in pausa e riprendere le attività; **creare, cambiare o rimuovere un'attività aspetta la tua approvazione** in [Journey](#/guide?s=journey).
- **Una scheda per attività**: un pallino colorato (verde = attiva), il nome, la pianificazione come viene salvata e i pulsanti *Run now*, *Pause* / *Resume*, *History*, *Edit*, *Delete*. Sotto il prompt e una riga: prossima esecuzione, ultimo risultato e quando, numero di esecuzioni e dove viene consegnato. *proposed by the model* indica le attività suggerite dall'assistente.
- **History** elenca le ultime esecuzioni (si conservano le ultime 50): stato (`ok`; `error`, che include il tempo scaduto; `missed`), quando, e l'output o l'errore.

## Scrivere la pianificazione

Il campo **When** capisce frasi normali in **italiano e inglese**, oltre al cron.

![Una frase in italiano capita come cron](/static/guide/img/schedules-2-sentence.png)

Mentre scrivi, una riga sotto il campo dice *Understood as `30 8 * * 1-5`* ed elenca le prossime cinque esecuzioni, così controlli prima di salvare. L'attività viene salvata come quell'espressione cron.

![Una frase in inglese](/static/guide/img/schedules-3-english.png)

| Scrivi | Viene salvato come |
|---|---|
| `ogni mattina alle 6` · `every morning at 6` | `0 6 * * *` |
| `ogni sera alle 8` · `every evening at 8` | `0 20 * * *` |
| `tutti i giorni alle 18:30` · `every day at 6:30 pm` | `30 18 * * *` |
| `ogni notte alle 2` · `every night at 2` | `0 2 * * *` |
| `ogni giorno alle 8 e alle 20` | `0 8,20 * * *` |
| `every day at 8am and 6pm` | `0 8,18 * * *` |
| `ogni giorno a mezzogiorno` · `every day at noon` | `0 12 * * *` |
| `ogni giorno alle 6 e mezza` | `30 6 * * *` |
| `nei giorni feriali alle 7` · `weekdays at 7` | `0 7 * * 1-5` |
| `dal lunedì al venerdì alle 8:30` · `from monday to friday at 8:30` | `30 8 * * 1-5` |
| `ogni lunedì e giovedì alle 9` · `every monday and thursday at 9am` | `0 9 * * 1,4` |
| `nel weekend alle 10` · `every weekend at 10` | `0 10 * * 0,6` |
| `ogni 2 ore` · `every 2 hours` | `0 */2 * * *` |
| `ogni 30 minuti` · `every 30 minutes` | `*/30 * * * *` |
| `ogni ora` · `every hour` | `0 * * * *` |
| `il primo del mese alle 8` · `the 1st of every month at 8` | `0 8 1 * *` |
| `il 15 del mese alle 9` | `0 9 15 * *` |
| `every month on the 15th at 10` | `0 10 15 * *` |

Funzionano ancora le altre forme: cinque campi cron (`0 8 * * 1-5`: minuto, ora, giorno del mese, mese, giorno della settimana), gli alias (`@hourly`, `@daily`, `@weekly`, `@monthly`) e `every 30m` / `every 2h` / `every 1d`.

**Se manca qualcosa il modulo lo dice.** "Ogni mattina" da solo viene rifiutato con *at what time?*; "ogni settimana" chiede *which day of the week?*; "l'ultimo giorno del mese" non si può scrivere in cron e suggerisce un giorno fino al 28; "ogni 2 ore alle 8" mescola un intervallo con un orario; più orari nella giornata devono avere gli stessi minuti (8:00 e 20:00, non 8:00 e 20:30: fai due attività). "Mattina" da sola non è mai un orario: AIFRED non indovina. Sera e pomeriggio aggiungono 12 ore a un numero minore di 12 ("ogni sera alle 8" è le 20:00), notte fa lo stesso da 6 a 11 ("alle 11 di notte" è le 23:00).

## Come fare

1. *New task*. Nome: lettere minuscole, cifre, `-` e `_`.
2. **When**: una frase, per esempio `ogni mattina alle 6`. Controlla la riga *Understood as*.
3. **What to do**: un'istruzione completa. Nessuno può rispondere a domande mentre gira, quindi di' tutto ciò che serve.
4. **Deliver the result to**: una chat Telegram della tua lista consentita, oppure *Do not deliver* (il risultato resta in History).
5. *Save*, poi *Run now* per provarla.

## Da sapere

- **Fuso orario.** Gli orari usano `SCHEDULER_TIMEZONE` (per esempio `Europe/Rome`; vuoto significa il fuso del server). I cambi tra ora legale e solare sono gestiti.
- **Non più spesso di ogni 5 minuti** (`SCHEDULER_MIN_INTERVAL`), al massimo 20 attività (`SCHEDULER_MAX_TASKS`), una esecuzione al massimo 10 minuti (`SCHEDULER_RUN_TIMEOUT`), due alla volta (`SCHEDULER_MAX_PARALLEL`).
- **Le esecuzioni senza supervisione sono limitate di proposito.** Usano la route `ROUTE_CRON` e solo i toolset di `TOOLSETS_CRON` (di default search, web e skills; si cambiano con la colonna *Scheduled* della pagina [Tools](#/guide?s=tools)). Non possono creare altre attività e non vengono riviste per la memoria.
- **Ogni slot gira una volta.** Un'esecuzione ancora in corso quando scatta la successiva viene saltata (resta solo nei Logs); uno slot perso di oltre 15 minuti (il server era spento) è registrato come `missed`, non eseguito. Un'esecuzione interrotta da un riavvio appare come errore.
- **La consegna è limitata alle chat di `TELEGRAM_ALLOWED_IDS`**, controllato al salvataggio e a ogni invio.
- Puoi usare anche `/cron` in qualsiasi chat ([Comandi di chat](#/guide?s=commands)).
