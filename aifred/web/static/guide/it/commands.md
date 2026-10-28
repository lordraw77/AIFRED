# Comandi di chat e Telegram

Un messaggio che inizia con `/` è un comando, non una domanda al modello. I comandi funzionano allo stesso modo nella chat web, su Telegram e nella riga di comando.

![La risposta a /help](/static/guide/img/commands-1-help.png)

## I comandi

| Comando | Cosa fa |
|---|---|
| `/start` | Un saluto. |
| `/help` | Elenca i comandi. |
| `/new` | Inizia una nuova conversazione (anche su Telegram). |
| `/sessions` | Elenca le conversazioni recenti. |
| `/think on\|off\|auto` | Attiva o disattiva il ragionamento del modello per questa conversazione. |
| `/model [route\|provider/model\|auto]` | Mostra o sceglie la route di questa conversazione. |
| `/kb [scan\|search <domanda>]` | Knowledge base: rilegge la cartella oppure cerca. |
| `/pending` | Elenca ciò che aspetta la tua approvazione. |
| `/approve <id>` / `/reject <id>` | Approva o scarta una voce in attesa. |
| `/mcp [reload]` | Mostra i server MCP e il loro stato; `reload` li riconnette. |
| `/refine` | Chiede all'assistente di rivedere ora la conversazione e salvare ciò che vale. |
| `/journey [delete <id>]` | Mostra ciò che l'assistente ha imparato; annulla una voce. |
| `/instructions [reload\|<nome>]` | Mostra a cosa serve ogni gruppo di strumenti. |
| `/assist <scope> [suggerimento]` | Scrive o migliora l'istruzione di un gruppo di strumenti (per esempio `/assist mcp:proxmox il mio cluster`), la prova e mostra il punteggio. Non salva nulla. |
| `/cron [list\|add\|pause\|resume\|run\|remove]` | Gestisce le attività pianificate (vedi sotto). |
| `/agent <nome> <compito>` | Affida un compito a un sub-agente. |
| `/agents` | Elenca i sub-agenti che puoi usare. |
| `/quit` | Esce dalla chat a riga di comando (solo riga di comando). |

### Pianificare da un comando
```
/cron add morning | ogni mattina alle 8 | Riassumi le notizie su Python
/cron add report | ogni lunedì alle 9 | Scrivi il report settimanale dei server
/cron list
/cron pause morning
/cron run morning
```
La seconda parte può essere una frase in italiano o in inglese, cinque campi cron, un alias come `@daily` o `every 30m`. Vedi [Schedules](#/guide?s=schedules).

## Telegram

1. Crea un bot con @BotFather e copia il token.
2. In [Config](#/guide?s=config) imposta `TELEGRAM_TOKEN` (è solo in scrittura: una volta salvato non viene più mostrato).
3. Scrivi al bot. Se il tuo id non è nella lista consentita, risponde con il tuo id numerico.
4. Metti quell'id in `TELEGRAM_ALLOWED_IDS` (più id si separano con virgole). La modifica vale subito.
5. La barra laterale e [System](#/guide?s=system) mostrano `running as @tuobot` quando funziona.

### Cosa sapere sul bot
- Risponde **solo nelle chat private**; gruppi e canali sono ignorati.
- Le risposte arrivano formattate (grassetto, corsivo, codice, link, elenchi). Se Telegram rifiuta la formattazione il messaggio viene rispedito come testo semplice, così non si perde nulla. Le risposte lunghe sono divise in più messaggi.
- Con la **lista consentita vuota il bot è aperto a tutti**. Gira allora in modalità ristretta: niente memoria né profilo, niente knowledge base, niente server MCP, niente terminale, un limite di frequenza, e solo i comandi `/start`, `/help`, `/new` e `/think`. La barra laterale avvisa *open to everyone*. Meglio una lista consentita.
- Le attività pianificate consegnano il risultato solo alle chat della lista consentita.
- Ogni chat Telegram ha la sua conversazione; `/new` ne inizia un'altra.
