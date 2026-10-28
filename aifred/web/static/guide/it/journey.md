# Journey

Il posto in cui resti al comando di ciò che l'assistente cambia da solo: cosa aspetta la tua approvazione, e una cronologia di tutto ciò che è stato imparato o cambiato, con annullamento.

![Approvazioni e cronologia](/static/guide/img/journey-1.png)

## Waiting for approval

Quando `WRITE_APPROVAL` in [Config](#/guide?s=config) è `memory`, `skills` o `all`, l'assistente non salva subito: le sue proposte aspettano qui. Ognuna mostra un numero, l'ora, la conversazione da cui viene e cosa vuole fare:

- una voce di **memoria** da aggiungere, cambiare o togliere;
- una **skill** da creare o cambiare;
- un'**istruzione** per un gruppo di strumenti;
- un'**attività pianificata** da creare, cambiare o rimuovere, con la sua pianificazione e dove andrebbe il risultato (le proposte di attività aspettano sempre, qualunque sia l'impostazione).

Premi **Approve** per applicarla o **Reject** per scartarla. Il testo viene analizzato di nuovo quando viene applicato. In chat puoi usare anche `/pending`, `/approve <id>` e `/reject <id>`.

## Timeline

Dalla più recente: l'ora, un badge per il tipo (`memory`, `skill`, `instruction`, `settings`…), l'azione, un riepilogo e uno stato:

- `applied` (fatto), `pending`, `approved`, `rejected`, `blocked` (il controllo di sicurezza l'ha rifiutata; il motivo è mostrato), `undone`.
- **Undo** compare sulle modifiche applicate di memoria, skill e istruzioni: rimette il testo precedente.

Compaiono anche le cose che fai tu: impostazioni cambiate, server MCP aggiunti, istruzioni salvate con il punteggio della loro prova.

## Da sapere

- Il controllo di sicurezza blocca testo che sembra un'istruzione iniettata, caratteri nascosti e tentativi di far uscire segreti, sia quando l'assistente propone sia di nuovo quando approvi.
- Attivare l'approvazione è una buona idea mentre impari a conoscere l'assistente; spegnila quando ti fidi.
