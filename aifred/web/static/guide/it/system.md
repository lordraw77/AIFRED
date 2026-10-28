# System

Uno sguardo veloce alla salute del server.

![Lo stato del server](/static/guide/img/system-1.png)

## Cosa vedi

- **Contatori**: versione, da quanto tempo è in funzione, conversazioni, messaggi e dimensione del database.
- **Gateways**: lo stato di ogni collegamento verso l'esterno.
  - **Telegram**: `running as @tuobot`, `starting`, `error` (con il motivo) o `stopped`; un avviso *open to everyone* se la lista consentita è vuota; e quando è arrivato l'ultimo messaggio.
  - **Knowledge base**: attiva o spenta, e quanti passaggi contiene.
  - **MCP · nome**: una riga per ogni server MCP con stato, numero di strumenti e protocollo, oppure l'errore.
- **Storage**: dove sta la cartella dei dati e il **backend dei segreti** (il file locale cifrato, oppure OpenBao) con il suo stato.

## Da sapere

- Gli stessi numeri di Telegram e delle conversazioni stanno in fondo al menu a sinistra, aggiornati ogni 15 secondi. Il pulsante *Restart* lì riconnette Telegram e i server MCP senza riavviare tutto il server.
- Se il backend dei segreti non è verde, token e chiavi non si possono leggere: provider, Telegram e server OAuth falliranno finché non torna.
