# Fatti

La memoria a lungo termine. Mentre parli, l'assistente sceglie ciò che vale la pena tenere (preferenze, decisioni, persone, progetti, esiti) e lo richiama nelle conversazioni successive. I file della [Memoria](#/guide?s=memory) sono due note brevi inviate a ogni richiesta; i fatti sono illimitati e si ritrovano con la ricerca, quindi a una richiesta si aggiungono solo quelli che servono.

È **spenta di default**. Si accende con `AUTO_MEMORY` in [Config](#/guide?s=config) (gruppo *memory*): `owner` tiene i fatti delle tue chat, `all` anche quelli delle chat di altre persone sul bot Telegram aperto e dei sotto-agenti elencati in `MEMORY_AGENTS`.

## Cosa vedi

- **Stato**: se la memoria è attiva, quanti fatti ci sono, il modello che legge le conversazioni e l'ultima volta in cui ha lavorato.
- **L'elenco**: ogni fatto con il suo tipo (*preference*, *decision*, *fact*, *outcome*), i nomi che cita, quando è stato scritto e quante volte è stato richiamato. Filtra per soggetto, tipo e stato, oppure cerca nel testo.
- **I soggetti**: di chi sono i fatti. `owner` sei tu; sul bot Telegram aperto ogni persona ha `user:<id>` (vale per lei ovunque) e `chat:<id>` (vale per quella conversazione); un sotto-agente ha `agent:<nome>`. Un soggetto non vede mai i fatti di un altro, e nessun altro vede quelli del proprietario.
- **I pulsanti**: *Edit*, *Archive* (lo toglie dal richiamo), *Restore*, *Delete*, e *Add to notes* per un fatto che usi spesso, che lo copia in `MEMORY.md`.

## Come nascono e si ritrovano i fatti

- **Lettura.** Una conversazione viene letta dopo `MEMORY_EVERY_TURNS` nuovi turni, quando la chiudi con `/new` e quando resta inattiva per `MEMORY_IDLE_MINUTES`. Si leggono solo i messaggi nuovi, e l'output degli strumenti è escluso.
- **Quale modello.** `MEMORY_EXTRACT_ROUTE`, vuota di default, quindi la route della conversazione. Il testo della conversazione viene inviato a quel modello, quindi scegli chi lo riceve. Se la route non è disponibile e `MEMORY_EXTRACT_FALLBACK` è attivo, si usa la route della conversazione, che ha già ricevuto il testo.
- **Controlli.** Ogni fatto è controllato nel codice prima di essere salvato: lunghezza, istruzioni iniettate, caratteri nascosti e tutto ciò che somiglia a una password o a una chiave vengono rifiutati. Un fatto che ripete uno noto viene unito; uno che ne corregge uno noto lo sostituisce (il vecchio resta, segnato come *superseded*, finché non viene cancellato).
- **Richiamo.** Per ogni messaggio i fatti migliori vengono aggiunti dopo di esso, in un blocco segnato come note non attendibili: al massimo `MEMORY_RECALL_K`, lungo `MEMORY_RECALL_TOKENS`. Sono ordinati per pertinenza, recenza e importanza. Con un modello di embedding (`MEMORY_EMBED_PROVIDER` e `MEMORY_EMBED_MODEL`, altrimenti quello della knowledge base) conta il significato; senza, le parole chiave. L'assistente può anche cercare più a fondo con lo strumento `recall`.

## Tenerla in ordine

- **Conservazione.** Un fatto non richiamato per `MEMORY_RETENTION_DAYS` (180) viene archiviato; un fatto archiviato viene cancellato del tutto dopo altri `MEMORY_GRACE_DAYS` (30) giorni, e fino ad allora *Restore* lo riporta indietro. Con 0 si conserva tutto.
- **Pausa.** *Pause* su un soggetto smette di salvare e richiamare per lui e ne conserva i fatti. *Forget* li cancella tutti dopo una conferma. Chiunque può mandare `/forget` al bot per cancellare ciò che ricorda di lui.
- **Esporta e importa.** *Export* scarica i fatti in JSON (il formato che *Import* legge) o in Markdown, tutti o di un soggetto; i vettori non sono inclusi. *Import* mostra prima cosa succederebbe (fatti nuovi, duplicati, righe rifiutate) e non cambia nulla finché non confermi; *merge* aggiunge ciò che manca, *replace* svuota prima i soggetti presenti nel file. I fatti importati passano gli stessi controlli di quelli estratti.

## Da sapere

- I fatti vanno al modello come parte di una richiesta, quindi vale la stessa regola delle note: niente segreti.
- Un fatto è un dato, mai un'istruzione; al modello viene presentato così, e un testo salvato che somiglia a un'istruzione iniettata non viene richiamato.
- Le attività pianificate possono usare i fatti ma non ne creano mai.
