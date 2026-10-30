# Usage

The Usage page answers "how much am I using, and what does it cost?". It adds up the model calls AIFRED made (chat, scheduled tasks, sub-agents, the review and the knowledge base) and shows them per provider.

## What you see

- **Period** (top): *Last 24 hours*, *7 days* or *30 days*.
- **Three totals**: tokens (with the split in and out), calls (and how many failed) and cost.
- **By provider**: calls, errors, tokens in and out, and cost, one row per provider, the heaviest first. Click a row to open its models and see the same numbers per model, with the price used.
- **Tokens per day**: a bar for each day, as wide as that day's tokens; hover a bar for the split by provider.

## Costs

AIFRED does not guess prices. The cost of a model is `tokens in × price in + tokens out × price out`, with the prices **per million tokens, in US dollars**, that you type on the [Models](#/guide?s=models) page: open a provider's models and fill *Price per 1M tokens (in / out)*. Look the figures up in the provider's price list (for Gemini, the Google AI pricing page) and enter the ones for the model you use.

- A model **without a price** counts tokens but has no cost: it shows *no price*. This is right for a local Ollama or vLLM and for a free tier.
- A provider's cost appears as soon as one of its models has a price; the totals say how many models still have none, so you know the figure is partial.
- Prices apply to the usage shown, also past usage: change a price and the whole period is recalculated.

## Good to know

- **Tokens come from the provider** when it reports them; otherwise they are estimated from the text, so a hosted provider's own bill is the reference.
- Only **successful calls** count tokens and cost. Failed calls and fallbacks are in *Errors*; the provider cards on [Models](#/guide?s=models) show the daily budget and the last error.
- The page is read-only. The per-day detail by model and agent is also available as `GET /api/usage`, and the summary shown here as `GET /api/usage/overview?days=N`.
