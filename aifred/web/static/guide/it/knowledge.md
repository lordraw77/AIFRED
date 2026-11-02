# Knowledge

I tuoi documenti (manuali, procedure, appunti, PDF, fogli di calcolo, pagine scansionate) resi ricercabili. Quando gli servono, l'assistente cerca qui e cita la fonte. Niente esce dalla tua macchina tranne il testo inviato al modello di embedding che hai scelto.

Questa pagina compare solo se la knowledge base è attiva: imposta `RAG_ENABLED` in [Config](#/guide?s=config) e scegli provider e modello di embedding (`RAG_EMBED_PROVIDER`, `RAG_EMBED_MODEL`). Si consiglia un modello di embedding locale.

![Stato, collezioni e documenti](/static/guide/img/knowledge-1-overview.png)

## Cosa vedi

### Status
Collezioni, passaggi (pezzi di testo), memoria usata dalla cache dei vettori e quanti documenti sono pronti. Sotto: lo stato della **route vision** (legge immagini e pagine scansionate; serve un modello che abbia superato *Test vision* nella pagina [Models](#/guide?s=models)) con il conteggio di oggi rispetto al limite giornaliero (`RAG_VISION_DAILY_LIMIT`, 200), e quale modello di embedding è usato e chi riceve il tuo testo.

### Collections
Una collezione è un gruppo di documenti con un nome ("homelab", "manuali"). La tabella mostra nome, descrizione, dimensione (documenti e passaggi), modello di embedding e stato. *Re-index* la ricostruisce (serve dopo aver cambiato il modello di embedding), *Delete* la elimina con tutti i documenti. La casella **Open bot** permette al bot Telegram aperto (quello senza allowlist) di cercare in quella collezione; serve anche `knowledge` in `TOOLSETS_TELEGRAM`, e una collezione non aperta per lui è invisibile. *Build index* costruisce subito l'indice approssimato (vedi sotto). Sotto: un nome, una descrizione e *Create*; e *Scan folder*.

### Documents
- Un filtro per collezione e l'elenco: titolo e nome file, **stato**, tipo, passaggi, dimensione, ultimo aggiornamento, *Delete*.
- **Stati**: `queued` (in attesa), `indexing`, `ready`, `waiting` (il servizio di embedding non è raggiungibile; AIFRED riprova), `partial` (è stato raggiunto un limite, la parte che ci stava è ricercabile), `error` (passa il mouse per il motivo).
- **Upload**: scegli la collezione, scegli i file, *Upload*. **Add URL**: AIFRED scarica la pagina o il documento.
- **Formati**: testo, Markdown, HTML, JSON, CSV, YAML; PDF; file Word, Excel, PowerPoint e OpenDocument, anche i vecchi `.doc`, `.xls` e `.ppt` (solo testo), messaggi e-mail (`.eml`) e libri EPUB; immagini JPG, PNG e WebP (lette dal modello vision). `RAG_FORMATS` sceglie quali accettare. Il limite di dimensione è `RAG_MAX_DOCUMENT_BYTES` (5 MB di default).

### Test search
Scrivi una domanda, scegli la modalità e premi `Invio`.

![Una ricerca di prova che mostra come è stato trovato ogni risultato](/static/guide/img/knowledge-2-search.png)

- **hybrid** (predefinita) unisce significato (dense) e parole chiave; **dense** solo il significato; **keyword** solo le parole.
- Ogni risultato mostra collezione, file e titolo, e dei badge: `dense #1`, `keyword #2` sono la posizione in ciascuna graduatoria; `rerank 0.93` compare quando un modello di rerank ha riordinato i candidati; `flagged` vuol dire che il documento ha fatto scattare il controllo anti-iniezione; `machine-read` che il testo viene da un modello vision, dal motore OCR o da una trascrizione.

### Retrieval quality
Misura se la ricerca trova il passaggio giusto, prima e dopo una modifica.

![Domande di riferimento e valutazione](/static/guide/img/knowledge-3-quality.png)

- **Domande di riferimento (golden questions)**: domande più il punto in cui sta la risposta: un documento (nome o parte del nome), una frase che il passaggio deve contenere, o entrambi. Aggiungile a mano, oppure premi **Write questions from my documents**: il modello legge alcuni passaggi a caso e scrive una domanda per ciascuno (i passaggi sono inviati al provider della route `assist`); tieni quelle buone con *Keep*.
- **Run evaluation**: scegli quanti risultati contano (top 1, 3, 5, 10), dai un'etichetta alla prova ("prima del rerank") e premi il pulsante. Ogni domanda viene cercata in quattro modi: `hybrid`, `dense`, `keyword` e `hybrid+rerank` (solo se è configurato un modello di rerank).
- **Recall** è la quota di domande il cui passaggio giusto era tra i risultati; **MRR** è più alto quanto più in alto stava. La tabella elenca le domande mancate. *Earlier runs* mostra ▲ e ▼ rispetto alla prova precedente della stessa ricerca, così vedi se una modifica ha aiutato.

## Come fare

- **Aggiungere documenti**: *Upload* o *Add URL*, oppure metti i file in `workspace/knowledge/<collezione>/` dentro la cartella dei dati e premi *Scan folder* (i file nuovi o cambiati vengono messi in coda, quelli spariti rimossi).
- **Usare un modello di rerank**: imposta `RAG_RERANK_PROVIDER` e `RAG_RERANK_MODEL` (un provider con un endpoint `/rerank`: Jina, Cohere, Voyage, vLLM). Poi confronta con *Run evaluation*.
- **Cambiare il modello di embedding**: cambialo in Config, poi *Re-index*; nel frattempo le ricerche trovano comunque i passaggi per parole chiave.

- **Far leggere una collezione al bot aperto**: spunta *Open bot* sulla collezione e aggiungi `knowledge` a `TOOLSETS_TELEGRAM` in Config. Solo le collezioni spuntate sono elencate o cercate da quel bot; il resto risponde come se non esistesse.
- **Indicizzare subito le modifiche**: attiva `RAG_WATCH`. Un file salvato, spostato o eliminato in `workspace/knowledge/<collezione>/` viene indicizzato un paio di secondi dopo (solo Linux; `RAG_SCAN_MINUTES` resta il ripiego).
- **Leggere il testo delle immagini su questa macchina**: imposta `RAG_OCR_ENGINE` su `tesseract` (sempre locale, gratis, senza limite, solo testo) o `auto` (prima tesseract, il modello vision se trova troppo poco). L'immagine Docker include tesseract con inglese e italiano (`RAG_OCR_LANGS`).
- **Indicizzare registrazioni**: aggiungi `audio` a `RAG_FORMATS`, scegli `RAG_AUDIO_PROVIDER` e `RAG_AUDIO_MODEL` (un endpoint `/audio/transcriptions` compatibile OpenAI: OpenAI, Groq, Mistral, un server Whisper locale). Si leggono MP3, WAV, M4A, Ogg, FLAC e WebM; ogni passaggio conserva il suo istante (`1:05`). Il suono va a quel provider: se conta, scegline uno locale.
- **Collezioni molto grandi**: da `RAG_ANN_MIN_CHUNKS` passaggi (500.000) un indice approssimato viene costruito in background e tenuto aggiornato; sotto quella soglia la ricerca è esatta. Alza `RAG_ANN_PROBE` per una precisione migliore.

## Da sapere

- L'assistente **legge soltanto** la knowledge base; non vi scrive mai. I passaggi sono segnati come non fidati: le istruzioni dentro un documento non vengono seguite.
- Ogni file viene aperto da un processo separato e limitato (niente rete, niente segreti, limiti di tempo e memoria).
- Il bot Telegram aperto non vede mai i tuoi documenti.
