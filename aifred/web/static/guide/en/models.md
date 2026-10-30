# Models

Where you say which models answer: providers (the services), their models, and routes (ordered lists with fallback). It also shows the health of every provider and what it has cost in tokens.

![Providers and their models](/static/guide/img/models-1-providers.png)

## Getting started

Until a provider, a tested model and a default route exist, a *Getting started* panel lists the three steps. It disappears when they are done.

## Providers

- **Add provider**: *Type* (a preset: hosted services, *Ollama*, any OpenAI-compatible *Local* server…), *Name* (a short name used in routes), *Base URL* (filled by the preset), *API key* (optional for a local engine; stored encrypted, never shown again), *Account id* (only for some presets), *TLS*. **Test and save** tests the connection first; *Save without testing the connection* skips it.
- **The card of a provider**: name, type, badges (*disabled*, *TLS not verified*) and the buttons:
  - **Test connection**: lists the models to check that it answers;
  - **Refresh models**: reads the model list again;
  - **Disable / Enable**: a disabled provider is skipped by every route;
  - **Edit**: base URL, TLS, and a **daily** and **monthly token budget** (empty means no limit; over the budget the provider is skipped);
  - **Delete**.
- **Keys**: a provider may have several keys; AIFRED uses the next when one fails. Each shows its last four characters and a status; *Replace*, *Delete*, or add another.
- **Models (N)**: the table of its models: id, kind (chat or embedding), context size, test result, vision.
  - **Test**: sends a request with a tool; a model that cannot call tools is *failed* and cannot go on a route. *Test embedding* checks an embedding model.
  - **Test vision**: shows the model a picture and checks it reads it (needed for the knowledge base's `vision` route).
  - *Add manually* adds a model id the provider did not list.

## Routes

![Routes, health and usage](/static/guide/img/models-2-routes-health.png)

A **route** is an ordered list of models. The first answers; if it fails (quota, outage, timeout) the next one does, and the failed one rests for a while (*cooldown*).

- Each route shows its models in order with arrows to move them up or down, an *x* to remove, a menu *Add a model…* (only tested models appear) and **Save order**.
- **Test route** sends a short request and shows who answered, how long it took and why the others failed.
- **Create route**: a name and a list. **Delete**.
- Ready-made names: `default` (what chat uses), `vision` (images and scanned pages of the knowledge base: no default, you choose who receives your images, put any paid provider last), and any other you create, such as `assist` (drafting instructions) or `compress` (summaries). Which route each gateway uses is set by `ROUTE_WEB`, `ROUTE_TELEGRAM`, `ROUTE_API`, `ROUTE_CLI`, `ROUTE_CRON` and `ROUTE_COMPRESS` in [Config](#/guide?s=config); `/model` changes it for one conversation.

## Prices

Each model row has **Price per 1M tokens (USD, in / out)**: two optional boxes. Fill them from the provider's price list to see costs on the [Usage](#/guide?s=usage) page; leave them empty for local models and free tiers.

## Health and usage

- **Health**: for each provider today's tokens, calls, errors and fallbacks, its budget, and the state of each model on each route (`healthy`, `cooldown`, or an error state, with the reason and a **Reset** button), and the last error.
- **Usage**: a table by day, model and agent (`default` conversations, `sub:<name>` sub-agents, `assist`, `rag`…) with calls, errors, tokens in and out and latency. Choose *Today*, *7 days* or *30 days*.

## Good to know

- Everything you write goes to the providers you configure; use *Ollama* or another local engine for private material.
- Only models that passed the tool test appear in the route menus.
- If a route has no working model the chat says so and points here.
