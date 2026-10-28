# Agents

Un sub-agente è uno specialista a cui l'assistente principale può affidare un compito: un ricercatore per il web, un sistemista per i tuoi server, uno scrittore per i testi. Ognuno ha istruzioni, modello, strumenti e limiti propri, lavora da solo sul compito e restituisce una risposta.

![L'elenco dei sub-agenti](/static/guide/img/agents-1-list.png)

## Cosa vedi

- **Let the main assistant delegate (toolset agents)**: interruttori che attivano il toolset `agents` per ogni gateway. Senza di esso l'assistente non ha lo strumento `delegate`.
- **Sub-agents** e **New agent**. La prima volta vengono creati tre agenti di partenza, *spenti*: `researcher` (web), `sysadmin` (i tuoi server, prima di tutto in sola lettura) e `writer` (testi).
- **Una scheda per agente**: pallino (verde = attivo), nome, badge con la route del modello e la modalità di memoria, poi *Turn on/off*, *Test*, *History*, *Edit*, *Delete*. Sotto: a cosa serve, toolset e skill, limiti.
- **Test**: scrivi un compito e premi *Run*; vedi la risposta, i token usati, il modello e la conversazione in cui è girato.
- **History**: le ultime esecuzioni con stato, orario, token, compito e output.

## Il modulo

![Il modulo di un nuovo agente](/static/guide/img/agents-2-form.png)

| Campo | Significato |
|---|---|
| **Name** | Lettere minuscole, cifre, `-` e `_`. |
| **What it is for** | Una riga che l'assistente principale legge per decidere quando affidare un compito. *Generate* e *Improve* aiutano a scriverla. |
| **Role** | Le istruzioni dell'agente. Sostituisce la personalità principale. L'agente non vede nulla della tua conversazione, solo il compito che riceve. *Generate*, *Improve* e *Shorten* aiutano. |
| **Model route** | Quale route risponde per questo agente: usa un modello più economico per lavori semplici. |
| **Toolsets** | Separati da virgola: `web`, `search`, `file`, `mcp:linux_ssh`… L'agente riceve solo ciò che hanno sia lui sia il gateway chiamante. |
| **Skills** | Skill precaricate nel suo prompt. |
| **Memory** | `none` (nessun accesso), `read` (vede i tuoi appunti e il profilo) o `write` (può anche salvare; vale `WRITE_APPROVAL`). |
| **Tool rounds / Time limit / Token budget** | Limiti di una esecuzione; vuoto usa i valori predefiniti (impostazioni `AGENT_*`). |

## Come fare

- **Usarne uno in chat**: attiva il toolset `agents` per *Web*, accendi un agente, poi chiedi: "chiedi al researcher di cercare…". Oppure scrivi `/agent researcher <compito>`.
- **Crearne uno tuo**: *New agent*, dagli un *What it is for* chiaro e un *Role*, scegli il minimo insieme di toolset che gli serve, premi *Test*.

## Da sapere

- **Un agente non ha mai più potere di chi lo chiama.** I suoi toolset sono l'intersezione tra i propri e quelli del gateway; non può mai usare `agents`, `cron` né la revisione in background, quindi non può delegare di nuovo né pianificare attività.
- **Non per il bot Telegram aperto**: la delega è rifiutata nelle chat aperte.
- **Tutto è registrato.** Ogni esecuzione è una conversazione figlia (sorgente `sub:<nome>`), collegata a quella che ha delegato, con i token conteggiati a parte nell'utilizzo di [Models](#/guide?s=models).
- Più compiti possono girare in parallelo (`delegate_many`, fino a `AGENT_MAX_PARALLEL`, 3). Limiti: `AGENT_MAX_ITERATIONS` (8), `AGENT_TIMEOUT` (300 s), `AGENT_MAX_AGENTS` (20).
