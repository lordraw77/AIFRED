# API keys

AIFRED parla l'API OpenAI, quindi altre app (Open WebUI, editor, script) possono usarlo come se fosse un modello. Questa pagina crea le chiavi che usano.

![Creare ed elencare le chiavi API](/static/guide/img/keys-1.png)

## Cosa vedi

- **New key**: *Name* (per ricordare chi la usa, per esempio "Open WebUI"), *Scope* e una data e ora di scadenza facoltativa. **Create key** mostra la chiave **una sola volta**, in un riquadro giallo con un pulsante *Copy*. Non viene più mostrata: ne viene conservato solo un hash.
- **Scope**: `chat` consente solo `/v1/chat/completions` (ciò che serve alle altre app); `admin` consente anche l'API della dashboard, quindi trattala come la tua password.
- **Keys**: nome, scope, quando è stata creata, ultimo uso e scadenza, con un badge *revoked* per le chiavi morte e **Revoke** (con conferma).
- **Use it**: un esempio `curl` con l'indirizzo di questo server.

## Come fare

1. Dai alla chiave il nome dell'app, scegli `chat`, *Create key*, copiala.
2. Nell'altra app imposta l'URL di base a `http://<tuo-server>:8700/v1` e la chiave come chiave API.
3. Il *model* che l'app invia è il nome di una route (`default`) oppure `provider/model`.

## Da sapere

- **La conversazione sta in AIFRED, non nell'app.** Di ogni richiesta si usa solo l'ultimo messaggio `user` (i messaggi precedenti e i `tools` dell'app vengono ignorati). La conversazione prende il nome dall'header `X-AIFRED-Session`, altrimenti dal campo `user`, altrimenti è `default`: le app che non inviano né l'uno né l'altro condividono una conversazione. Compare in [Sessions](#/guide?s=sessions) come `api:<nome>`.
- Revoca una chiave nel momento in cui trapela; l'app che la usa riceve subito un errore.
- Le richieste usano i toolset del gateway *API* (vedi [Tools](#/guide?s=tools)) e la route `ROUTE_API`.
