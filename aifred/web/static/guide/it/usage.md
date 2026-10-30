# Utilizzo

La pagina Utilizzo risponde a "quanto sto usando e quanto costa?". Somma le chiamate ai modelli fatte da AIFRED (chat, attività programmate, sotto-agenti, la revisione e la base di conoscenza) e le mostra per provider.

## Cosa vedi

- **Periodo** (in alto): *Last 24 hours*, *7 days* o *30 days*.
- **Tre totali**: token (divisi in ingresso e uscita), chiamate (e quante sono fallite) e costo.
- **By provider**: chiamate, errori, token in ingresso e in uscita e costo, una riga per provider, dal più usato. Un clic sulla riga apre i suoi modelli con gli stessi numeri per modello e il prezzo usato.
- **Tokens per day**: una barra per giorno, larga in proporzione ai token di quel giorno; passando sopra una barra vedi la divisione per provider.

## I costi

AIFRED non indovina i prezzi. Il costo di un modello è `token in ingresso × prezzo in + token in uscita × prezzo out`, con i prezzi **per milione di token, in dollari USA**, che scrivi nella pagina [Models](#/guide?s=models): apri i modelli di un provider e compila *Price per 1M tokens (in / out)*. Cerca le cifre nel listino del provider (per Gemini, la pagina dei prezzi di Google AI) e inserisci quelle del modello che usi.

- Un modello **senza prezzo** conta i token ma non ha costo: mostra *no price*. È corretto per un Ollama o vLLM locale e per un piano gratuito.
- Il costo di un provider compare appena un suo modello ha un prezzo; i totali dicono quanti modelli ne sono ancora privi, così sai che la cifra è parziale.
- I prezzi valgono per l'utilizzo mostrato, anche passato: cambi un prezzo e tutto il periodo viene ricalcolato.

## Da sapere

- **I token arrivano dal provider** quando li comunica; altrimenti sono stimati dal testo, quindi il riferimento resta la fattura del provider.
- Solo le **chiamate riuscite** contano token e costo. Le chiamate fallite e i passaggi al modello successivo sono negli *Errors*; le schede dei provider in [Models](#/guide?s=models) mostrano il limite giornaliero e l'ultimo errore.
- La pagina è in sola lettura. Il dettaglio per giorno, modello e agente è disponibile anche come `GET /api/usage`, e il riepilogo mostrato qui come `GET /api/usage/overview?days=N`.
