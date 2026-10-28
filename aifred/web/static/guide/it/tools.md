# Tools

Quali strumenti l'assistente può usare su ogni gateway, quante volte sono stati usati e *a cosa serve* ogni gruppo di strumenti.

![La matrice dei toolset](/static/guide/img/tools-1-matrix.png)

## La matrice

- **Una riga per toolset**, con il numero di strumenti. Un clic sul nome elenca gli strumenti: ciascuno con la descrizione, un badge `open bot ok` se può usarlo il bot Telegram aperto, `not available now` quando richiede qualcosa che non è impostato, e quante chiamate ha avuto dall'avvio del server.
- **Una colonna per gateway**: *CLI*, *WEB* (la chat di questa dashboard), *API*, *TELEGRAM* e *CRON* (attività pianificate).
- **Clicca un interruttore** per attivare o disattivare un toolset per quel gateway. Gli elenchi della pagina [Config](#/guide?s=config) vengono aggiornati per te. Un gateway che non aveva un elenco proprio ne riceve una copia di quello condiviso con la tua modifica, così gli altri non si muovono.
- La colonna **Calls** conta gli usi dall'avvio del server.

### I toolset
`memory` (appunti e profilo), `skills`, `search` (cerca nelle conversazioni passate) e `web` (ricerca sul web e lettura di pagine), `file` (leggere e scrivere nella cartella di lavoro), `terminal` (comandi di shell; spento di default e da usare con cautela), `knowledge` (i tuoi documenti), `cron` (attività pianificate), `agents` (delegare ai sub-agenti) e un `mcp:<server>` per ogni server MCP.

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
- **Test** simula la scelta del modello: scrive circa otto richieste di prova (alcune che richiedono il gruppo, alcune quasi simili, alcune che non richiedono strumenti), invia ciascuna al modello con gli strumenti veri e legge solo il primo strumento scelto. **Nessuno strumento viene eseguito.** Ottieni un punteggio e un esito pass/fail per richiesta, più il punteggio del testo salvato per confronto.
- **Refine and test** lascia che il modello riscriva il testo fino a tre volte partendo dai fallimenti e tiene quello col punteggio migliore; decidi tu se usarlo.
- **Save**, **Delete**, e **Notes for single tools** sotto per note di una riga sui singoli strumenti. Un testo accettato viene registrato in [Journey](#/guide?s=journey) col suo punteggio (per esempio `[test 5/6]`).

## Da sapere

- I test richiedono che gli strumenti del gruppo siano attivi per *WEB*: altrimenti la pagina ti dice di attivarli.
- Al massimo 10 valutazioni all'ora (`ASSIST_EVALUATIONS_PER_HOUR`): ognuna chiama molte volte il modello. Il provider della route `assist` (o `default`) riceve nomi e descrizioni degli strumenti.
- In chat, `/assist mcp:proxmox` fa bozza, prova e punteggio in un colpo solo.
