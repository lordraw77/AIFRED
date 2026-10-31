# Tools

Quali strumenti l'assistente può usare su ogni gateway, quante volte sono stati usati e *a cosa serve* ogni gruppo di strumenti.

![Le schede dei toolset](/static/guide/img/tools-1-matrix.png)

## Le schede dei toolset

- **Una scheda per toolset**, con il nome, il numero di strumenti e le chiamate dall'avvio del server. Un clic sul nome elenca gli strumenti: ciascuno con la descrizione, un badge `open bot ok` se può usarlo il bot Telegram aperto, e `not available now` quando richiede qualcosa che non è impostato.
- **Un interruttore per gateway** nella scheda: *CLI*, *WEB* (la chat di questa dashboard), *API*, *TELEGRAM* e *CRON* (attività pianificate).
- **Clicca un interruttore** per attivare o disattivare un toolset per quel gateway. Gli elenchi della pagina [Config](#/guide?s=config) vengono aggiornati per te. Un gateway che non aveva un elenco proprio ne riceve una copia di quello condiviso con la tua modifica, così gli altri non si muovono.
- Le chiamate sono il contatore in alto a destra di ogni scheda: conta gli usi dall'avvio del server.

### I toolset
`memory` (appunti e profilo), `skills`, `search` (cerca nelle conversazioni passate) e `web` (ricerca sul web e lettura di pagine), `file` (leggere e scrivere nella cartella di lavoro), `terminal` (comandi di shell; spento di default e da usare con cautela), `ssh` (comandi sui tuoi host via SSH: `ssh_hosts`, `ssh_run`; spento di default, vedi sotto), `knowledge` (i tuoi documenti), `library` (le risposte che hai salvato: `library_search`, `library_get`, `library_save`), `cron` (attività pianificate), `agents` (delegare ai sub-agenti) e un `mcp:<server>` per ogni server MCP.

## Instructions: what is it for?

Lo schema di uno strumento dice *come* chiamarlo, non *perché* esiste. Qui scrivi, per ogni server MCP e gruppo di strumenti, **quando usarlo e quando no**. L'assistente lo legge nel prompt (la sezione *Capabilities*) e può chiedere il testo completo con `tool_help`.

- **Il contatore** (*Capabilities section: 239 / 3000 characters*) mostra quanto del budget `INSTRUCTIONS_BUDGET` è usato. Oltre il budget il testo viene accorciato automaticamente.
- **What the agent sees** apre la sezione esatta.
- **Apply to open conversations** fa recepire le modifiche alle conversazioni in corso.
- **Una riga per gruppo** con badge: `documented`, `undocumented` (un server MCP senza nulla scritto), `not used` (scritto ma spento). *Edit* lo apre.

### L'editor

![Scrivere un'istruzione e provarla](/static/guide/img/tools-2-test.png)

- **Il testo** e il suo contatore (circa 600 caratteri è una buona misura), e *Use it in the prompt* per tenerlo senza inviarlo.
- **Hint for the draft**: poche parole ("i miei server Linux locali e Proxmox") per orientare il modello.
- **Generate** scrive una bozza dagli strumenti che il gruppo offre e dalle altre tue istruzioni; **Improve** riscrive il testo attuale mantenendo ciò che è giusto; **Shorten** lo accorcia. Vedi una bozza e scegli *Use this text* o *Discard*. Non si salva nulla finché non premi *Save*.
- **Use my profile and notes for the draft** invia al modello che scrive la bozza anche la tua memoria (nomi, intervalli di indirizzi); è spento di default.
- **Use my recent chats that missed this target** invia anche fino a sei richieste recenti dalle tue chat (web, CLI, API; mai un bot Telegram aperto) che sembrano legate al gruppo ma hanno avuto risposta senza usarlo, così la bozza copre ciò che all'assistente è sfuggito; è spento di default.
- **Test** simula la scelta del modello: scrive circa otto richieste di prova (alcune che richiedono il gruppo, alcune quasi simili, alcune che non richiedono strumenti), invia ciascuna al modello con gli strumenti veri e legge solo il primo strumento scelto. **Nessuno strumento viene eseguito.** Ottieni un punteggio e un esito pass/fail per richiesta, più il punteggio del testo salvato per confronto.
- **Refine and test** lascia che il modello riscriva il testo fino a tre volte partendo dai fallimenti e tiene quello col punteggio migliore; decidi tu se usarlo.
- **Save**, **Delete**, e **Notes for single tools** sotto per note di una riga sui singoli strumenti. Un testo accettato viene registrato in [Journey](#/guide?s=journey) col suo punteggio (per esempio `[test 5/6]`).

## Da sapere

- I test richiedono che gli strumenti del gruppo siano attivi per *WEB*: altrimenti la pagina ti dice di attivarli.
- Al massimo 10 valutazioni all'ora (`ASSIST_EVALUATIONS_PER_HOUR`): ognuna chiama molte volte il modello. Il provider della route `assist` (o `default`) riceve nomi e descrizioni degli strumenti.
- In chat, `/assist mcp:proxmox` fa bozza, prova e punteggio in un colpo solo.

## Terminale remoto via SSH

Il toolset `ssh` permette all'assistente di eseguire un comando su uno dei **tuoi host** (`ssh_hosts` li elenca, `ssh_run` esegue un comando). È spento di default: aggiungi `ssh` ai toolset dei gateway di cui ti fidi e compila due impostazioni.

- `SSH_HOSTS`: un oggetto JSON, una voce per host, per esempio `{"pve1": {"user": "root", "host": "10.70.0.1", "port": 22, "host_key": "ssh-ed25519 AAAA…", "description": "Nodo Proxmox"}}`. L'assistente sceglie l'host per nome e non può mai raggiungere un indirizzo non elencato.
- `SSH_PRIVATE_KEY`: una chiave privata senza passphrase, usata per tutti gli host. Dalle un account con i soli diritti che servono all'assistente.

`host_key` è obbligatoria: si ottiene con `ssh-keyscan -t ed25519 10.70.0.1` (la parte dopo l'indirizzo). La connessione è rifiutata se il server presenta un'altra chiave e nulla viene imparato al primo accesso. Vale la stessa lista di comandi distruttivi bloccati del terminale locale, più un timeout (`SSH_TIMEOUT`) e un limite all'output. Un bot Telegram aperto non ottiene mai il toolset.

## Domande dai server MCP

Un server della revisione 2026-07-28 può interrompere una chiamata per chiederti qualcosa (un breve modulo, per esempio "quale progetto?"). L'assistente te lo chiede in chat e prosegue la chiamata con `mcp_reply`. I moduli per password, token o chiavi sono sempre rifiutati, `MCP_ELICITATION` spegne la funzione e `MCP_SAMPLING` (spento di default) permette a un server di far scrivere del testo al modello dell'assistente. Questi server possono anche tenere aperto uno stream (`MCP_SUBSCRIPTIONS`) così un elenco di strumenti cambiato è rilevato subito.
