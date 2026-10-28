# Sessions

Tutte le conversazioni di AIFRED, da ogni gateway, in un unico posto: la chat web, Telegram, l'API, le attività pianificate e i sub-agenti.

![Il quadro generale delle sessioni](/static/guide/img/sessions-1-overview.png)

## Cosa vedi

- **I quattro contatori** in alto: conversazioni totali, messaggi totali, quante sorgenti (web, telegram, cron, api, sub-agenti) e quante piattaforme sono attive.
- **Il filtro per sorgente**: mostra solo le conversazioni di una sorgente (`web`, `telegram`, `cron`, `sub:researcher`…).
- **Overview / history**: due viste della stessa pagina. La scelta viene ricordata.

### Overview
- **Connected platforms**: `api_server` (l'API HTTP e questa dashboard) e `telegram` con il suo stato e quando ha ricevuto qualcosa l'ultima volta.
- **Recent sessions**: titolo, sorgente, numero di messaggi, ultimo aggiornamento e un'anteprima delle ultime parole. Un clic su una riga la apre in [Chat](#/guide?s=chat) (in sola lettura se non è una conversazione web).
- **La matita su ogni riga** apre un piccolo menu: una casella e *Save title* per rinominare, *Export* per scaricare la conversazione come file Markdown, *Delete* (con conferma).

### History
Cerca nel testo di tutte le conversazioni: scrivi nella casella e premi `Invio` (il tasto `/` porta alla casella). Ogni risultato mostra la conversazione, la sorgente, chi ha parlato e quando, con le tue parole evidenziate. Un clic apre quella conversazione.

![La ricerca nella cronologia](/static/guide/img/sessions-2-history.png)

## Come fare

- **Ritrovare qualcosa detto settimane fa**: History, una parola caratteristica, `Invio`.
- **Conservare una conversazione fuori da AIFRED**: matita, *Export*.
- **Fare ordine**: matita, *Delete*. Cancellare una conversazione elimina anche quelle dei sub-agenti che ha avviato. I messaggi vengono rimossi definitivamente.

## Da sapere

- Le conversazioni avviate da un'attività pianificata hanno sorgente `cron` e titolo *Scheduled: nome*. Le esecuzioni dei sub-agenti hanno sorgente `sub:<agente>` e sono collegate alla conversazione che ha delegato.
- Le conversazioni lunghe vengono riassunte per il modello (vedi [Chat](#/guide?s=chat)) ma qui resta la registrazione completa.
