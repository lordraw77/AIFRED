# MCP

MCP (Model Context Protocol) è un modo standard con cui i programmi offrono strumenti a un assistente. Un server MCP può dare ad AIFRED gli strumenti per guardare il tuo cluster Proxmox, eseguire comandi sui tuoi host Linux, controllare Home Assistant e così via. Ogni server diventa un toolset chiamato `mcp:<nome>`.

![L'elenco dei server MCP](/static/guide/img/mcp-1-servers.png)

## Cosa vedi

- **Servers**, con **Reload** (riconnette tutti) e **Add server**.
- **Una scheda per server**:
  - il pallino e il badge di stato: `connected`, `connecting`, `error` o `disabled`, il protocollo parlato e gli avvisi (*TLS not verified*);
  - **Test**: si connette subito e riporta il protocollo, il numero di strumenti e il tempo impiegato. **Edit**, **Delete** (con conferma);
  - l'indirizzo (o `command:` per un programma) e *N tools exposed · latenza · call timeout*;
  - **Use in**: interruttori che attivano o disattivano gli strumenti di questo server per *CLI*, *Web*, *API*, *Telegram* e *Scheduled*, oppure *On everywhere*. Gli elenchi di [Config](#/guide?s=config) vengono aggiornati per te. Un gateway senza elenco proprio ne riceve una copia di quello condiviso, così gli altri non si muovono. (Se un gateway usa l'intero gruppo `mcp` devi toglierlo in Config per scegliere i server uno a uno.)
  - **What is this for?**: l'istruzione che l'assistente legge per sapere quando usare questo server (vedi [Tools](#/guide?s=tools)), con *Edit*;
  - **Dropped tools** (uno strumento descritto male dal server viene escluso, con il motivo) e i filtri *only these / never these*;
  - per i server OAuth, la riga di accesso (sotto).

## Aggiungere un server

![Il modulo di un nuovo server](/static/guide/img/mcp-2-form.png)

| Campo | Significato |
|---|---|
| **Name** | `a-z`, `0-9` e `_`, fino a 24 caratteri. Diventa il toolset `mcp:<nome>` e parte del nome di ogni strumento. |
| **URL** | L'indirizzo del server, `http://` o `https://`. Le credenziali non vanno nell'URL: usa un header. |
| **or Command** | Al posto dell'URL: un programma che AIFRED avvia (vedi *Programmi (stdio)* sotto). |
| **TLS** | Verificare il certificato, non verificarlo (autofirmato; un avviso resta sulla scheda), o usare un tuo file CA. |
| **Call timeout / Connect timeout** | Secondi; vuoto usa `MCP_CALL_TIMEOUT` (120) e `MCP_CONNECT_TIMEOUT` (15). |
| **Tool cap** | Il massimo di strumenti presi da questo server (default `MCP_MAX_TOOLS_PER_SERVER`, 40). |
| **Protocol** | `auto` (consigliato: prova la revisione più recente, poi le precedenti) oppure una da forzare. |
| **Only these tools / Never these tools** | Filtri separati da virgola. |
| **What is this for?** | Obbligatorio per un server nuovo: di' quando l'assistente deve usarlo e quando no. *Generate* aiuta una volta salvato il server. |
| **Headers** | Per esempio `Authorization: Bearer …`. I valori sono cifrati e non vengono più mostrati; lascia vuoto un valore in modifica per tenerlo. |
| **Enabled** / **Save without testing** | I server attivi si connettono; di norma il salvataggio prova prima la connessione. |
| **Sign in with OAuth** | Vedi sotto. |

Premi **Test and save**.

## Programmi (stdio)

Al posto di un URL, un server può essere un **comando** che AIFRED avvia e con cui parla tramite standard input e output, per esempio `uvx some-mcp-server`. È potente, quindi è **spento finché non elenchi il programma** in `MCP_STDIO_COMMANDS` in Config (per nome di file). Il programma riceve un ambiente minimo (nessuna variabile o segreto di AIFRED; solo le variabili che imposti per lui, nell'area *Headers*, cifrate), una sua cartella privata, e viene fermato insieme a tutto ciò che ha avviato. Gira dentro il container di AIFRED con i suoi privilegi, e l'immagine standard contiene solo Python, quindi installa prima ciò che ti serve.

## OAuth

Alcuni server vogliono che tu acceda con un provider nel browser. Spunta **Sign in with OAuth**, salva e premi **Authorize** sulla scheda: il provider si apre in una nuova scheda, accetti, e AIFRED conserva i token cifrati e li rinnova. La scheda mostra *OAuth: signed in*; **Forget the sign-in** rimuove i token.

Per un prodotto che ha una voce già pronta (Gmail), la pagina [Connectors](#/guide?s=connectors) lo fa per te, compresi client id, client secret e permessi richiesti dal provider.

Serve un'identità del client: o un client id registrato a mano presso il provider (`MCP_OAUTH_CLIENT_ID`), oppure, se AIFRED è raggiungibile da internet in https, il suo indirizzo pubblico in `MCP_OAUTH_PUBLIC_URL` (AIFRED serve allora il documento di metadati del client che il provider scarica). Una rete di casa non raggiungibile da fuori richiede la prima.

## Da sapere

- I server si riconnettono da soli: uno irraggiungibile viene riprovato dopo 5, 10, 20, 40 e 60 secondi e non blocca mai nulla. Gli strumenti vengono riletti regolarmente; se la definizione di uno strumento cambia viene registrato in [Journey](#/guide?s=journey).
- Le descrizioni e i risultati degli strumenti di un server sono **dati non fidati**: l'assistente non segue mai le istruzioni che vi trova.
- Il bot Telegram aperto non ha mai strumenti MCP.
- Un server che chiede altro input a metà chiamata (sampling, elicitation) non è supportato: al modello viene detto che l'ha chiesto.
