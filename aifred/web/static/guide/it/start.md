# Primi passi

AIFRED è un assistente personale che gira sul tuo hardware. Ci parli dalla dashboard, da Telegram o da qualsiasi app che usi l'API OpenAI. Può ricordare cose, leggere i tuoi documenti, usare strumenti sui tuoi server, eseguire attività pianificate e affidare lavoro a sub-agenti specializzati. Questa guida percorre una per una tutte le pagine della dashboard.

![La pagina di accesso](/static/guide/img/start-1-login.png)

## Accedere

- Il nome utente è quello impostato in `AIFRED_ADMIN_USER` (di default `admin`).
- Al primo avvio la password è quella di `AIFRED_ADMIN_PASSWORD` nel file `.env`. Dopo il primo avvio l'account esiste nel database: togli la password da `.env` e cambiala dalla pagina [Account](#/guide?s=account).
- Troppi tentativi sbagliati bloccano l'accesso per un po': il messaggio dice per quanto.

## I primi dieci minuti

1. **Aggiungi un provider.** Apri [Models](#/guide?s=models), premi *Add provider*, scegli un preset (un servizio in cloud, oppure *Ollama* per un modello sul tuo computer), incolla la chiave API e premi *Test and save*.
2. **Prova un modello.** Apri l'elenco *Models* del provider e premi *Test* su un modello. Deve rispondere a una richiesta con uno strumento prima che AIFRED lo usi.
3. **Mettilo sulla route di default.** In *Routes* aggiungi il modello provato a `default` e premi *Save order*. Una route è un elenco ordinato di modelli: se il primo fallisce risponde il successivo.
4. **Saluta.** Apri [Chat](#/guide?s=chat) e scrivi un messaggio.
5. **Facoltativo: Telegram.** In [Config](#/guide?s=config) imposta `TELEGRAM_TOKEN` (da @BotFather) e `TELEGRAM_ALLOWED_IDS` (il tuo id numerico di Telegram: scrivi una volta al bot e ti dice l'id). Vedi [Comandi di chat e Telegram](#/guide?s=commands).

## Le parole che incontrerai

| Parola | Significato |
|---|---|
| **Provider** | Un servizio che esegue modelli: un'API in cloud o un motore locale come Ollama. |
| **Model** | Un modello di un provider. Deve superare una prova con gli strumenti prima dell'uso. |
| **Route** | Un elenco ordinato di modelli con ripiego automatico. `default` è usata dalla chat; `vision`, `compress` e altre sono facoltative. |
| **Gateway** | Un modo di parlare con AIFRED: la chat web, Telegram, l'API, la riga di comando e le attività pianificate. |
| **Toolset** | Un gruppo di strumenti (`memory`, `web`, `file`, `agents`, `cron`, `mcp:<server>`…). Ogni gateway ha il proprio elenco di toolset attivi. |
| **Memory** | Appunti brevi che l'assistente conserva tra una conversazione e l'altra (`MEMORY.md`) e su di te (`USER.md`). |
| **Skill** | Una procedura salvata che l'assistente può caricare quando un compito la richiede. |
| **MCP server** | Un programma esterno che offre altri strumenti (per esempio i tuoi host Proxmox o Linux). |
| **Approval** | Con `WRITE_APPROVAL` attivo, ciò che l'assistente vuole salvare o pianificare aspetta il tuo sì in [Journey](#/guide?s=journey). |

## Orientarsi

- Il menu a sinistra ha una voce per pagina. Il piccolo pulsante *Guide* in alto a destra di ogni pagina apre la parte di questa guida che la riguarda.
- Tastiera: premi `g` poi `c`, `s`, `m`, `k` o `j` per saltare a Chat, Sessions, Memory, Skills o Journey; `/` porta alla casella di ricerca o di filtro; `Esc` chiude il menu sul telefono.
- In fondo al menu: lo stato di Telegram, il numero di conversazioni, *Restart* (riavvia il poller di Telegram e riconnette i server MCP), il pulsante del tema (auto, teal, sand) e *Sign out*. La freccia in alto comprime il menu.
- Tutto funziona anche sul telefono: il menu diventa un cassetto che si apre col pulsante in alto a sinistra.

## Dove stanno i dati

Tutto è nella cartella dei dati (`/data` nell'immagine Docker): database, file di memoria, skill, cartella knowledge e segreti cifrati. Fai il backup di quella cartella. Le chiavi API dei provider sono cifrate e non vengono più mostrate.

Avanti: [Chat](#/guide?s=chat).
