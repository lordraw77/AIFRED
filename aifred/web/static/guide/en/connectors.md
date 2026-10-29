# Connectors

Ready-made MCP servers of products you use, set up in a few steps. The first one is **Gmail** (Google's own Gmail MCP server); more will be added over time. A connector is an ordinary [MCP](#/guide?s=mcp) server underneath: once it is set up and you have signed in, its tools appear on the [Tools](#/guide?s=tools) page and you switch them on for the gateways you want.

![The Connectors page](/static/guide/img/connectors-1-catalog.png)

## What you see

- **One card per product** with a state badge: `not set up`, `needs sign-in`, `connected · N tools` or an error.
- **How to set it up**: the steps to take at the product's side. They are open for a product you have not set up yet.
- **Redirect address to register**: the address the product sends you back to after you accept. Copy it exactly into the app you register at the product. A warning appears when the product will not accept it (see *The redirect address* below).
- **Client id** and **Client secret**: from the app you registered. The secret is stored encrypted and is never shown again; leave the field empty on a later *Save* to keep it.
- **What the assistant may do**: the permissions to ask for. Each one is a real capability of the assistant, so keep only what you need.
- **Save**, then **Authorize** (or *Sign in again*): the product opens in a new tab, you accept, and this page updates by itself. **Forget the sign-in** drops the tokens; **Remove** deletes the connector, its tokens and its client.
- **Let the assistant use…**: the switches that turn the connector's tools on for *CLI*, *Web*, *API*, *Telegram* and *Scheduled*. Without them the assistant does not see the tools.

![A connector saved and waiting for the sign-in](/static/guide/img/connectors-2-saved.png)

## How to

### Connect Gmail
1. In the [Google Cloud console](https://console.cloud.google.com/) create or pick a project and enable two APIs for it: the **Gmail API** and the **Gmail MCP API**.
2. Open *Google Auth Platform > Branding* and set up the consent screen if it is not yet: any app name, your email, and the audience *Internal*. If you cannot choose Internal (a personal Gmail account) choose *External* and add yourself as a test user under *Audience*.
3. Open *Google Auth Platform > Clients > Create client*. Choose the application type **Web application**, add as an authorized redirect URI the address shown on the card, and tick **Use this client for an AI-powered agent**.
4. Under *Data access* add the permissions you will choose here, then *Save*.
5. Copy the client id and the client secret into the card, tick the permissions, press **Save**, then **Authorize** and accept in the Google tab.
6. Switch Gmail on for the gateways you want with the *Let the assistant use Gmail* switches, then ask in the chat: "what did Anna write about the move?".

**What it can do.** Google's Gmail server has tools to search threads, read a thread, list labels and drafts, create a draft and label or unlabel messages. It has **no tool that sends mail or deletes it**. *Read mail* and *Write drafts* are on by default: the assistant can search and read, and prepare drafts that you send yourself from Gmail. *Organise* adds labelling (Google's scope for it also allows moving mail to the trash). Turn on only what you need.

### The redirect address
Google accepts a redirect address only if it is `https`, or `http` on `localhost`. If you open the dashboard by the address of the machine (for example `http://192.168.0.5:8700`) the card shows a warning. Either open the dashboard as `http://localhost:8700` through an SSH tunnel (`ssh -L 8700:localhost:8700 your-host`) and do the sign-in from there, or serve AIFRED over https and set `MCP_OAUTH_PUBLIC_URL` on the [Config](#/guide?s=config) page: the address on the card then follows it.

### Stay signed in
Google gives an app whose publishing status is **Testing** a sign-in that expires after 7 days. Publish the app (or use an *Internal* audience in Google Workspace) so that AIFRED can renew the sign-in by itself. AIFRED asks for offline access, so a renewable sign-in is issued.

## When it does not work

- **`redirect_uri_mismatch`**: the address registered at Google is not exactly the one on the card. Copy it again.
- **"Access blocked" or "not verified"**: add your Google account as a test user under *Audience*.
- **The token endpoint says `client_secret is missing` or `invalid_client`**: paste the secret again and press *Save*; check it belongs to the same client id.
- **Connected but the assistant never reads mail**: the toolset is off for that gateway; use the switches on the card. The open Telegram bot never has MCP tools.

## Good to know

- The client id, the permissions and the tokens are kept per connector. The tokens and the secret are in the encrypted secret store; the card never shows them.
- A connector creates an MCP server of the same name (`gmail`). If one with that name already exists with another address, the card tells you to rename or delete it first.
- The text the tools return (mail, subjects, senders) is **untrusted data**: the assistant does not follow instructions written inside an email.
