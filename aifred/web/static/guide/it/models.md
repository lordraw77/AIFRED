# Models

Qui dici quali modelli rispondono: provider (i servizi), i loro modelli e le route (elenchi ordinati con ripiego). Mostra anche lo stato di ogni provider e quanti token ha usato.

![I provider e i loro modelli](/static/guide/img/models-1-providers.png)

## Getting started

Finché non esistono un provider, un modello provato e una route di default, un pannello *Getting started* elenca i tre passaggi. Sparisce quando sono fatti.

## Providers

- **Add provider**: *Type* (un preset: servizi in cloud, *Ollama*, qualsiasi server *Local* compatibile OpenAI…), *Name* (un nome breve usato nelle route), *Base URL* (compilato dal preset), *API key* (facoltativa per un motore locale; cifrata, non viene più mostrata), *Account id* (solo per alcuni preset), *TLS*. **Test and save** prova prima la connessione; *Save without testing the connection* la salta.
- **La scheda di un provider**: nome, tipo, badge (*disabled*, *TLS not verified*) e i pulsanti:
  - **Test connection**: elenca i modelli per verificare che risponda;
  - **Refresh models**: rilegge l'elenco dei modelli;
  - **Disable / Enable**: un provider disattivato è saltato da ogni route;
  - **Edit**: base URL, TLS e un **budget di token giornaliero** e **mensile** (vuoto significa nessun limite; oltre il budget il provider viene saltato);
  - **Delete**.
- **Keys**: un provider può avere più chiavi; AIFRED usa la successiva quando una fallisce. Ognuna mostra gli ultimi quattro caratteri e uno stato; *Replace*, *Delete*, oppure aggiungine un'altra.
- **Models (N)**: la tabella dei suoi modelli: id, tipo (chat o embedding), dimensione del contesto, esito della prova, vision.
  - **Test**: invia una richiesta con uno strumento; un modello che non sa chiamare strumenti risulta *failed* e non può stare su una route. *Test embedding* prova un modello di embedding.
  - **Test vision**: mostra al modello un'immagine e verifica che la legga (serve per la route `vision` della knowledge base).
  - *Add manually* aggiunge un id di modello che il provider non ha elencato.

## Routes

![Route, stato e utilizzo](/static/guide/img/models-2-routes-health.png)

Una **route** è un elenco ordinato di modelli. Risponde il primo; se fallisce (quota, guasto, timeout) risponde il successivo, e quello fallito riposa per un po' (*cooldown*).

- Ogni route mostra i suoi modelli in ordine con frecce per spostarli su e giù, una *x* per toglierli, un menu *Add a model…* (compaiono solo i modelli provati) e **Save order**.
- **Test route** invia una breve richiesta e mostra chi ha risposto, quanto ci ha messo e perché gli altri hanno fallito.
- **Create route**: un nome e un elenco. **Delete**.
- Nomi già pronti: `default` (quella che usa la chat), `vision` (immagini e pagine scansionate della knowledge base: nessun valore predefinito, scegli tu chi riceve le tue immagini, metti per ultimo un provider a pagamento), e ogni altra che crei, come `assist` (scrittura di istruzioni) o `compress` (riassunti). Quale route usa ogni gateway lo decidono `ROUTE_WEB`, `ROUTE_TELEGRAM`, `ROUTE_API`, `ROUTE_CLI`, `ROUTE_CRON` e `ROUTE_COMPRESS` in [Config](#/guide?s=config); `/model` la cambia per una conversazione.

## Prezzi

Ogni riga di modello ha **Price per 1M tokens (USD, in / out)**: due caselle facoltative. Compilale con il listino del provider per vedere i costi nella pagina [Usage](#/guide?s=usage); lasciale vuote per i modelli locali e i piani gratuiti.

## Health and usage

- **Health**: per ogni provider i token, le chiamate, gli errori e i ripieghi di oggi, il budget, e lo stato di ogni modello su ogni route (`healthy`, `cooldown`, o uno stato di errore, con il motivo e un pulsante **Reset**), e l'ultimo errore.
- **Usage**: una tabella per giorno, modello e agente (conversazioni `default`, sub-agenti `sub:<nome>`, `assist`, `rag`…) con chiamate, errori, token in entrata e in uscita e latenza. Scegli *Today*, *7 days* o *30 days*.

## Da sapere

- Tutto ciò che scrivi va ai provider che configuri; per materiale riservato usa *Ollama* o un altro motore locale.
- Nei menu delle route compaiono solo i modelli che hanno superato la prova degli strumenti.
- Se una route non ha nessun modello funzionante la chat lo dice e rimanda qui.
