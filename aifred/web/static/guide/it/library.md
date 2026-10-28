# Libreria

Il posto per le risposte e i risultati che vuoi conservare: una ricetta, un piano, una checklist, un rapporto scritto da un agente. La salvi una volta; poi, quando la chiedi in chat o serve a un agente, l'assistente legge il testo salvato da qui invece di scriverlo di nuovo.

![Salvare una risposta dalla chat](/static/guide/img/library-1-save.png)

## Cosa vedi

- **Le voci e il contatore** (`3 / 500 items`): ogni testo salvato, l'ultimo modificato per primo. Il limite è `LIBRARY_MAX_ITEMS`.
- **Use in**: interruttori che accendono il toolset `library` per ogni gateway (CLI, Web, API, Telegram, Scheduled). Senza, l'assistente non può leggere né salvare voci della libreria lì. Il bot Telegram aperto non ce l'ha mai.
- **La casella di ricerca**: trova le voci per parole nel titolo, nel testo e nei tag; gli accenti non contano. **I pulsanti dei tag** sotto filtrano l'elenco; premine uno di nuovo per togliere il filtro.
- **Una riga per voce**: il titolo, i tag, da dove viene (*from a chat*, *from an agent*, *written here*) e quando è cambiata, con le prime parole sotto. Clicca la riga per aprirla.
- **Una voce aperta** mostra il testo formattato, poi i pulsanti *Copy*, *Edit*, *Regenerate*, *Restore previous* (solo dopo una modifica) e *Delete* (con conferma), e la richiesta che l'ha prodotta.
- **New item**: scrivine una a mano (titolo, tag, richiesta, testo).

![La libreria con una voce aperta](/static/guide/img/library-2-list.png)

## Come fare

### Conservare una risposta della chat
Sotto ogni risposta dell'assistente c'è **Save to library**. Premilo: il titolo è precompilato dalla risposta (cambialo se vuoi), aggiungi dei tag se ti servono, premi *Save*. La richiesta che hai fatto viene salvata col testo, così può essere rigenerata. Se il titolo esiste già, il pulsante diventa *Replace it*: il testo vecchio resta per un annullamento.

Puoi anche chiederlo a parole: "salva questa ricetta come *Riso fritto*". L'assistente usa lo strumento `library_save`. Salva solo quando lo chiedi.

### Conservare il risultato di un agente
Quando l'assistente affida un compito a un sub-agente, la risposta compare come riga di strumento nella chat; aprila e premi **Save to library**. Nella pagina [Agents](#/guide?s=agents) lo stesso pulsante sta sotto il risultato di *Test* e sotto ogni esecuzione in *History*.

### Ritrovarla
Chiedi e basta: "dammi la ricetta del riso fritto". Il prompt di sistema elenca i titoli delle voci più recenti e l'assistente cerca (`library_search`) e legge la migliore (`library_get`), poi ti dà il testo salvato così com'è. Funziona in chat, su Telegram (per te, non per il bot aperto), nell'API e per i sub-agenti che hanno il toolset `library`.

### Riscriverla
Apri una voce e premi **Regenerate**. AIFRED chiede di nuovo con la richiesta salvata, in una conversazione usa e getta che usa lo stesso modello e gli stessi strumenti della chat web, e mostra il testo nuovo sotto quello vecchio come bozza. *Use this version* sostituisce il testo (il vecchio resta: *Restore previous* lo riporta, e premendolo ancora si torna avanti); *Discard* butta la bozza. Nulla cambia finché non premi *Use this version*. Una voce senza richiesta salvata non si può rigenerare: premi *Edit* e scrivi la richiesta nel campo *Request*.

![Una bozza rigenerata accanto al testo salvato](/static/guide/img/library-3-regenerate.png)

### Modificare a mano
*Edit* apre titolo, tag, richiesta e testo. Salvare un testo cambiato conserva il vecchio per un annullamento.

## Impostazioni

| Impostazione | Cosa fa |
|---|---|
| `LIBRARY_ITEM_CHARS` | Voce più grande (20.000 caratteri di default). |
| `LIBRARY_MAX_ITEMS` | Massimo di voci (500 di default). |
| `LIBRARY_PROMPT_ITEMS` | Quanti titoli recenti sono elencati nel prompt di sistema (30 di default; 0 non ne elenca, l'assistente può comunque cercare). |

## Da sapere

- **È testo dell'utente, non istruzioni.** Ciò che l'assistente legge dalla libreria è trattato come ogni risultato di strumento: è un dato. Un salvataggio fatto dall'assistente viene controllato come una voce di memoria e rifiutato se sembra un'istruzione iniettata.
- **Approvazione.** Con `WRITE_APPROVAL` su `all`, i salvataggi dell'assistente aspettano in [Journey](#/guide?s=journey). Ciò che salvi tu con i pulsanti qui non viene mai trattenuto.
- **Non è Memory né Knowledge.** [Memory](#/guide?s=memory) sono pochi fatti brevi inviati a ogni richiesta; la base [Knowledge](#/guide?s=knowledge) sono i tuoi documenti, cercati per passaggi; la libreria sono testi interi che hai scelto di tenere, letti solo quando una richiesta corrisponde.
- **Dimensione.** Nel prompt entrano solo i titoli delle voci più recenti; i testi si leggono a richiesta, quindi una libreria grande non costa nulla finché non serve.
