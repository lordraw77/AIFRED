# Chat

La chat è il modo principale di usare AIFRED dal browser. Le risposte arrivano mentre vengono scritte, gli strumenti girano a metà risposta e tutto viene salvato come conversazione.

![Una conversazione con una chiamata a uno strumento e una tabella nella risposta](/static/guide/img/chat-1-conversation.png)

## Cosa vedi

- **New** (in alto a sinistra): apre una conversazione vuota. La precedente resta nell'elenco.
- **L'elenco delle conversazioni**: le conversazioni web, dalla più recente (fino a 50), con le prime parole del primo messaggio come titolo e il numero di messaggi. Un clic la apre. Passando il mouse su una riga compare il cestino; premerlo chiede *Delete / No*, così un clic sbagliato non cancella nulla. Cancellare elimina anche le conversazioni dei sub-agenti avviati da quella conversazione.
- **I messaggi**: *You* sopra, *AIFRED* sotto. Il testo dell'assistente è formattato: titoli, elenchi, tabelle, citazioni, codice, grassetto e corsivo, e link che si aprono in una nuova scheda. L'HTML non viene mai interpretato.
- **Le righe degli strumenti**, come `mcp__proxmox__list_vms()`: ogni volta che l'assistente usa uno strumento compare una riga. Un clic ne apre il risultato, un altro lo chiude. Il simbolo ▸ vuol dire chiuso, ▾ aperto.
- **La riga di utilizzo** sotto una risposta: quale modello ha risposto e quanti token ha usato la richiesta.
- **La casella in basso**: `Invio` invia, `Maiusc+Invio` va a capo. Scrivere `/` inizia un comando (vedi [Comandi di chat e Telegram](#/guide?s=commands)). Mentre una risposta viene scritta il pulsante *Send* diventa *Stop*.

![Il risultato di uno strumento aperto](/static/guide/img/chat-2-tool-result.png)

## Come fare

### Chiedere qualcosa che richiede uno strumento
Chiedi con parole normali: "elenca le VM del cluster Proxmox". L'assistente decide quale strumento chiamare. Se non usa mai uno strumento che ti aspettavi, controlla in [Tools](#/guide?s=tools) che il toolset sia attivo per la chat web e che la sua istruzione dica quando usarlo.

### Affidare un compito a un sub-agente
Dillo: "chiedi al researcher di cercare le ultime notizie sulla Roma". L'assistente usa lo strumento `delegate`, che compare come riga; la sua risposta è chiusa, così un report lungo non sommerge la conversazione. Aprila per leggere la risposta intera. Servono il toolset `agents` e un agente attivo ([Agents](#/guide?s=agents)).

![La risposta di un sub-agente, chiusa e aperta](/static/guide/img/chat-3-agent-answer.png)

### Leggere una conversazione che viene da un altro posto
Le conversazioni nate su Telegram, in un'attività pianificata o in un sub-agente si aprono da [Sessions](#/guide?s=sessions). Si aprono in sola lettura: la casella spiega che puoi iniziare qui una nuova conversazione.

## Da sapere

- **Le conversazioni lunghe vengono riassunte.** Quando una conversazione supera circa 12.000 token, i messaggi più vecchi sono condensati in appunti (impostazioni `COMPRESS_*`) e un avviso lo dice. Non si cancella nulla: la pagina mostra ancora tutti i messaggi.
- **Avvisi.** Piccoli messaggi compaiono come notifiche: una voce di memoria salvata, un ripiego di modello, un riassunto fatto. `NOTIFICATIONS` in [Config](#/guide?s=config) li spegne o li rende dettagliati.
- **Approvazioni.** Con `WRITE_APPROVAL` attivo, i salvataggi di memoria e skill non avvengono subito: aspettano in [Journey](#/guide?s=journey).
- **Il modello.** La chat web usa la route `ROUTE_WEB` (di default `default`). `/model` la cambia per una conversazione.
- **Riservatezza.** Tutto ciò che scrivi va al provider della route, come con qualsiasi modello in cloud. Per materiale sensibile usa un provider locale.
