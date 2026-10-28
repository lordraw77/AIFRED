# Memory

Due piccoli file che l'assistente legge all'inizio di ogni conversazione: ciò che sa, e ciò che sa di te. Sono ciò che gli permette di ricordare da una conversazione all'altra.

![I due file di memoria](/static/guide/img/memory-1.png)

## Cosa vedi

- **What I know (`MEMORY.md`)**: fatti durevoli sul tuo mondo: indirizzi, orari, decisioni. Limite predefinito: 2.200 caratteri (`MEMORY_LIMIT`).
- **About you (`USER.md`)**: il tuo nome, le preferenze, come ti piace ricevere le risposte. Limite predefinito: 1.375 caratteri (`USER_LIMIT`).
- **Il contatore e la barra** di ogni file: caratteri usati e percentuale del limite. Quando un file è pieno l'assistente deve togliere qualcosa prima di salvare altro.
- **Il grafo** (sotto i due file): ogni nome scritto tra doppie parentesi quadre è un punto; due nomi nella stessa voce sono uniti da una linea. Clicca un punto per vedere le sue voci.
- **Le voci**: una per riga, ciascuna con *Edit* e *Remove* (con conferma).
- **La casella in basso** e *Add*: scrivi tu una voce.

## Come fare

- **Aggiungere un fatto**: scrivilo nella casella, premi *Add*. Frasi brevi e concrete funzionano meglio ("Il router è su 10.70.0.1").
- **Correggerne uno**: *Edit*, cambia il testo, *Save*.
- **Lasciarlo fare all'assistente**: digli "ricorda che…". Usa lo strumento `memory`; una notifica dice cosa è stato salvato. Può anche salvare cose dopo una conversazione (la revisione in background, `REVIEW_ENABLED`).

## Nomi e grafo

![Il grafo sotto i file di memoria](/static/guide/img/memory-2.png)

- Scrivi tra doppie parentesi quadre le persone, gli host, i progetti e i luoghi di cui parla una voce: `[[Marco]] lavora da [[Acme]]`. Scrivi sempre un nome nello stesso modo; all'assistente è chiesto di farlo e di riusare i nomi già presenti nelle note.
- La dimensione di un punto è il numero di voci che lo citano; il colore dice dove stanno (note, profilo o entrambi). Una linea significa che due nomi condividono una voce; più spessa, più voci.
- Le voci scritte prima di questa regola entrano nel grafo se citano, in parole semplici, un nome che altre voci mettono tra parentesi. Le voci senza nomi sono contate sotto il grafo ma non disegnate.
- I file restano la fonte: il grafo viene ridisegnato da loro ogni volta, quindi modificare o togliere una voce lo cambia subito.

## Dimensione

- Entrambi i limiti sono impostazioni: `MEMORY_LIMIT` per le note e `USER_LIMIT` per il profilo, tra 300 e 50.000 caratteri ([Config](#/guide?s=config), gruppo *agent*). Il link sotto ogni contatore apre Config.
- Tutto il contenuto viene inviato con **ogni richiesta**, quindi un file più grande costa token ogni volta. Alza un limite solo se la barra è spesso piena.
- Abbassare un limite sotto ciò che è già salvato mantiene le voci ma rifiuta le nuove finché non ne togli o unisci alcune.

## Come l'assistente sceglie i fatti

La revisione in background legge ciò che hai detto (i tuoi messaggi per intero; le sue risposte lunghe accorciate se la conversazione è lunga), lo confronta con le note e con la dimensione di ogni file, poi aggiunge, sostituisce o toglie. Scrive un fatto per voce, con date assolute e nomi tra parentesi, unisce i quasi-duplicati e fa ordine quando un file supera l'85%. `/refine` la avvia a mano.

## Da sapere

- **Approvazione.** Con `WRITE_APPROVAL` su `memory` o `all`, i salvataggi dell'assistente aspettano in [Journey](#/guide?s=journey) finché non li approvi. Le tue modifiche qui non vengono mai trattenute.
- **Controllo di sicurezza.** Tutto ciò che entra in memoria, da te o dall'assistente, viene analizzato: testo che sembra un'istruzione iniettata ("ignora le istruzioni precedenti…"), caratteri nascosti e tentativi di far uscire segreti vengono rifiutati.
- **Annullare.** Ogni modifica è nella cronologia di Journey con un pulsante *Undo*.
- **Va al modello.** La memoria fa parte di ogni richiesta: tienici lontano i segreti. Il bot Telegram aperto non la vede mai.
