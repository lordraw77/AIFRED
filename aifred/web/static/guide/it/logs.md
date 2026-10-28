# Logs

Cosa sta facendo il server AIFRED, in diretta: connessioni, chiamate agli strumenti, errori, esecuzioni pianificate, Telegram, MCP. Utile quando qualcosa non funziona.

![Il log in diretta](/static/guide/img/logs-1.png)

## Cosa vedi

- **Level**: `DEBUG`, `INFO`, `WARNING` o `ERROR`. Si mostrano le righe di quel livello e superiori (la scelta viene ricordata). `INFO` è il predefinito.
- **Pause / Resume**: smette di aggiungere nuove righe alla vista (il server continua a registrare).
- **Follow**: tiene la vista sull'ultima riga; togli la spunta per leggere con calma.
- **Clear view**: svuota solo la vista.
- **Le righe**: ora, livello, la parte di AIFRED che l'ha scritta e il messaggio. Nella vista restano le ultime 500 righe e quelle nuove arrivano in diretta.

## Da sapere

- Chiavi API, token e password non vengono mai scritti nel log; i messaggi di errore ne vengono ripuliti.
- Il livello che il server registra è `LOG_LEVEL` in [Config](#/guide?s=config); questa pagina filtra soltanto ciò che viene mostrato.
- Se gli aggiornamenti in diretta si fermano (per esempio sei stato disconnesso) una riga di avviso lo dice.
