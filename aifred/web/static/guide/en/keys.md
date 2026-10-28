# API keys

AIFRED speaks the OpenAI API, so other apps (Open WebUI, editors, scripts) can use it as if it were a model. This page creates the keys they use.

![Creating and listing API keys](/static/guide/img/keys-1.png)

## What you see

- **New key**: *Name* (to remember who uses it, for example "Open WebUI"), *Scope* and an optional *Expires* date and time. **Create key** shows the key **once**, in a yellow box with a *Copy* button. It is never shown again: only a hash of it is kept.
- **Scope**: `chat` allows only `/v1/chat/completions` (what other apps need); `admin` also allows the dashboard's own API, so treat it like your password.
- **Keys**: name, scope, when it was created, last used and when it expires, with a *revoked* badge for dead keys and **Revoke** (with a confirmation).
- **Use it**: a `curl` example with this server's address.

## How to

1. Name the key after the app, choose `chat`, *Create key*, copy it.
2. In the other app set the base URL to `http://<your-server>:8700/v1` and the key as the API key.
3. The *model* the app sends is a route name (`default`) or `provider/model`.

## Good to know

- **The conversation lives in AIFRED, not in the app.** Only the last `user` message of each request is used (earlier messages and the app's own `tools` are ignored). The conversation is named by the `X-AIFRED-Session` header, else by the `user` field, else it is `default`: apps that send neither share one conversation. It appears in [Sessions](#/guide?s=sessions) as `api:<name>`.
- Revoke a key the moment it leaks; the app using it gets an error at once.
- Requests use the toolsets of the *API* gateway (see [Tools](#/guide?s=tools)) and the route `ROUTE_API`.
