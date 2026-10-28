# Memory

Due piccoli file che l'assistente legge all'inizio di ogni conversazione: ciò che sa, e ciò che sa di te. Sono ciò che gli permette di ricordare da una conversazione all'altra.

![I due file di memoria](/static/guide/img/memory-1.png)

## Cosa vedi

- **What I know (`MEMORY.md`)**: fatti durevoli sul tuo mondo: indirizzi, orari, decisioni. Limite: 2.200 caratteri.
- **About you (`USER.md`)**: il tuo nome, le preferenze, come ti piace ricevere le risposte. Limite: 1.375 caratteri.
- **Il contatore e la barra** di ogni file: caratteri usati e percentuale del limite. Quando un file è pieno l'assistente deve togliere qualcosa prima di salvare altro.
- **Le voci**: una per riga, ciascuna con *Edit* e *Remove* (con conferma).
- **La casella in basso** e *Add*: scrivi tu una voce.

## Come fare

- **Aggiungere un fatto**: scrivilo nella casella, premi *Add*. Frasi brevi e concrete funzionano meglio ("Il router è su 10.70.0.1").
- **Correggerne uno**: *Edit*, cambia il testo, *Save*.
- **Lasciarlo fare all'assistente**: digli "ricorda che…". Usa lo strumento `memory`; una notifica dice cosa è stato salvato. Può anche salvare cose dopo una conversazione (la revisione in background, `REVIEW_ENABLED`).

## Da sapere

- **Approvazione.** Con `WRITE_APPROVAL` su `memory` o `all`, i salvataggi dell'assistente aspettano in [Journey](#/guide?s=journey) finché non li approvi. Le tue modifiche qui non vengono mai trattenute.
- **Controllo di sicurezza.** Tutto ciò che entra in memoria, da te o dall'assistente, viene analizzato: testo che sembra un'istruzione iniettata ("ignora le istruzioni precedenti…"), caratteri nascosti e tentativi di far uscire segreti vengono rifiutati.
- **Annullare.** Ogni modifica è nella cronologia di Journey con un pulsante *Undo*.
- **Va al modello.** La memoria fa parte di ogni richiesta: tienici lontano i segreti. Il bot Telegram aperto non la vede mai.
