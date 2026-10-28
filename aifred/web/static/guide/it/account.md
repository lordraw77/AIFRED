# Account

L'account amministratore della dashboard: cambia la password e vedi, o termina, i browser connessi.

![Password e sessioni connesse](/static/guide/img/account-1.png)

## Cosa vedi

- **Password**: *Current password*, *New password* due volte, **Change password**. Un cambio riuscito disconnette tutti gli altri browser.
- **Signed-in sessions**: ogni browser o dispositivo connesso: una descrizione del browser, il suo indirizzo, quando è stato visto l'ultima volta, un badge *this one* per quello che stai usando e **Sign out** per gli altri.

## Come fare

- **Cambia la password** dopo il primo avvio e togli `AIFRED_ADMIN_PASSWORD` dal file `.env`: serve solo a creare l'account.
- **Liberarti di una sessione dimenticata**: *Sign out* accanto ad essa.

## Da sapere

- Troppe password sbagliate bloccano l'accesso per un po', per indirizzo.
- Il cookie di sessione è `HttpOnly` e la dashboard controlla un token a ogni modifica, quindi un altro sito non può agire al posto tuo.
- Se perdi la password, esegui `docker compose exec aifred python -m aifred admin reset-password` sul server.
