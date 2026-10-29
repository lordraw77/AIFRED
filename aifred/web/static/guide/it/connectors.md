# Connectors

Server MCP già pronti per i prodotti che usi, da configurare in pochi passi. Il primo è **Gmail** (il server MCP di Gmail di Google); altri arriveranno col tempo. Un connettore è, sotto, un normale server [MCP](#/guide?s=mcp): quando è configurato e hai fatto l'accesso, i suoi strumenti compaiono nella pagina [Tools](#/guide?s=tools) e li accendi per i gateway che vuoi.

![La pagina Connectors](/static/guide/img/connectors-1-catalog.png)

## Cosa vedi

- **Una scheda per prodotto**, con un badge di stato: `not set up`, `needs sign-in`, `connected · N tools` oppure un errore.
- **How to set it up**: i passi da fare dal lato del prodotto. Sono aperti per un prodotto non ancora configurato.
- **Redirect address to register**: l'indirizzo a cui il prodotto ti riporta dopo che accetti. Copialo identico nell'app che registri presso il prodotto. Compare un avviso quando il prodotto non lo accetterebbe (vedi *L'indirizzo di reindirizzamento* sotto).
- **Client id** e **Client secret**: quelli dell'app che hai registrato. Il secret è conservato cifrato e non viene più mostrato; lascia il campo vuoto in un *Save* successivo per tenerlo.
- **What the assistant may do**: i permessi da chiedere. Ognuno è una capacità reale dell'assistente, quindi tieni solo ciò che ti serve.
- **Save**, poi **Authorize** (o *Sign in again*): il prodotto si apre in una nuova scheda, accetti, e questa pagina si aggiorna da sola. Quando la dashboard è aperta in http semplice con l'indirizzo della macchina compare una casella *After accepting: the address of the page the browser ends on* e concludi con **Finish** (vedi *L'indirizzo di reindirizzamento*). **Forget the sign-in** elimina i token; **Remove** cancella il connettore, i suoi token e il suo client.
- **Let the assistant use…**: gli interruttori che accendono gli strumenti del connettore per *CLI*, *Web*, *API*, *Telegram* e *Scheduled*. Senza di essi l'assistente non vede gli strumenti.

![Un connettore salvato, in attesa dell'accesso](/static/guide/img/connectors-2-saved.png)

## Come fare

### Collegare Gmail
1. Nella [console Google Cloud](https://console.cloud.google.com/) crea o scegli un progetto e abilita per esso due API: la **Gmail API** e la **Gmail MCP API**.
2. Apri *Google Auth Platform > Branding* e configura la schermata di consenso se non lo è già: un nome qualsiasi per l'app, la tua email e il pubblico *Interno*. Se non puoi sceglierlo (un account Gmail personale) scegli *Esterno* e aggiungiti come utente di test in *Pubblico*.
3. Apri *Google Auth Platform > Client > Crea client*. Scegli il tipo **Applicazione web**, aggiungi come URI di reindirizzamento autorizzato l'indirizzo mostrato nella scheda e spunta **Use this client for an AI-powered agent**.
4. In *Accesso ai dati* aggiungi i permessi che sceglierai qui, poi *Salva*.
5. Copia client id e client secret nella scheda, spunta i permessi, premi **Save**, poi **Authorize** e accetta nella scheda di Google.
6. Accendi Gmail per i gateway che vuoi con gli interruttori *Let the assistant use Gmail*, poi chiedi in chat: "cosa ha scritto Anna sul trasloco?".

**Cosa può fare.** Il server Gmail di Google ha strumenti per cercare thread, leggere un thread, elencare etichette e bozze, creare una bozza ed etichettare o togliere l'etichetta ai messaggi. **Non ha alcuno strumento che invii o elimini posta.** *Read mail* e *Write drafts* sono accesi di default: l'assistente può cercare e leggere, e preparare bozze che invii tu da Gmail. *Organise* aggiunge le etichette (lo scope di Google per questo permette anche di spostare la posta nel cestino). Accendi solo ciò che ti serve.

### L'indirizzo di reindirizzamento
Google accetta un indirizzo di reindirizzamento solo se è `https`, oppure `http` su `localhost`. AIFRED sceglie il modo adatto a come hai aperto la dashboard, e la scheda ti dice quale:

- **Automatico.** La dashboard è aperta in `https`, oppure come `http://localhost:…`: la scheda mostra il suo indirizzo, lo registri, e l'accesso si conclude da solo dopo che accetti. Per usare https servi AIFRED con un nome dietro il tuo reverse proxy (per esempio `https://aifred.example.org`) e imposta `MCP_OAUTH_PUBLIC_URL` nella pagina [Config](#/guide?s=config); quel nome deve aprirsi solo nel *tuo* browser, non da internet.
- **Copia l'indirizzo.** La dashboard è aperta con l'indirizzo della macchina in http semplice (per esempio `http://192.168.0.5:8700`): la scheda mostra invece `http://localhost:8700/api/mcp/oauth/callback`, ed è quello da registrare. Premi **Authorize** e accetta nella scheda di Google. Il browser prova allora ad aprire `localhost:8700`, non ci riesce e mostra una pagina di errore: è previsto. Copia l'**indirizzo intero** dalla barra degli indirizzi, incollalo nella casella sotto il form e premi **Finish**. Non serve nessun tunnel e funziona da qualsiasi computer.

### Restare connessi
Google dà a un'app con stato di pubblicazione **Testing** un accesso che scade dopo 7 giorni. Pubblica l'app (o usa un pubblico *Interno* in Google Workspace) così AIFRED può rinnovare l'accesso da solo. AIFRED chiede l'accesso offline, quindi viene rilasciato un accesso rinnovabile.

## Quando non funziona

- **`redirect_uri_mismatch`**: l'indirizzo registrato presso Google non è identico a quello nella scheda. Copialo di nuovo.
- **"This sign-in is unknown or has expired"** quando premi *Finish*: un accesso dura 10 minuti e vale una volta sola. Premi di nuovo *Authorize* e incolla il nuovo indirizzo.
- **"Accesso bloccato" o "non verificata"**: aggiungi il tuo account Google come utente di test in *Pubblico*.
- **Il token endpoint dice `client_secret is missing` o `invalid_client`**: incolla di nuovo il secret e premi *Save*; controlla che appartenga allo stesso client id.
- **Connesso ma l'assistente non legge mai la posta**: il toolset è spento per quel gateway; usa gli interruttori nella scheda. Il bot Telegram aperto non ha mai strumenti MCP.

## Da sapere

- Client id, permessi e token sono conservati per connettore. I token e il secret stanno nell'archivio dei segreti cifrato; la scheda non li mostra mai.
- Un connettore crea un server MCP con lo stesso nome (`gmail`). Se ne esiste già uno con quel nome e un altro indirizzo, la scheda ti dice di rinominarlo o eliminarlo prima.
- Il testo che gli strumenti restituiscono (posta, oggetti, mittenti) è **dato non fidato**: l'assistente non segue le istruzioni scritte dentro una email.
