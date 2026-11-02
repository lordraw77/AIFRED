# Knowledge

Your own documents (manuals, runbooks, notes, PDFs, spreadsheets, scanned pages) made searchable. When it needs them the assistant searches this base and cites the source. Nothing leaves your machine except the text sent to the embedding model you chose.

This page appears only when the knowledge base is turned on: set `RAG_ENABLED` in [Config](#/guide?s=config) and choose the embedding provider and model (`RAG_EMBED_PROVIDER`, `RAG_EMBED_MODEL`). A local embedding model is recommended.

![Status, collections and documents](/static/guide/img/knowledge-1-overview.png)

## What you see

### Status
Collections, passages (chunks of text), the memory used by the vector cache and how many documents are ready. Under it: the state of the **vision route** (it reads images and scanned pages; it needs a model that passed *Test vision* on the [Models](#/guide?s=models) page) with today's count against the daily limit (`RAG_VISION_DAILY_LIMIT`, 200), and which embedding model is used and who receives your text.

### Collections
A collection is a named group of documents ("homelab", "manuals"). The table shows name, description, size (documents and passages), the embedding model and the state. *Re-index* rebuilds it (needed after changing the embedding model), *Delete* removes it with all its documents. The **Open bot** box lets the open Telegram bot (the one with an empty allowlist) search that collection; it also needs `knowledge` in `TOOLSETS_TELEGRAM`, and a collection you did not open is invisible to it. *Build index* makes the approximate index now (see below). Below: a name, a description and *Create*; and *Scan folder*.

### Documents
- A filter by collection and the list: title and file name, **state**, kind, passages, size, last update, *Delete*.
- **States**: `queued` (waiting), `indexing`, `ready`, `waiting` (the embedding service is unavailable; AIFRED retries), `partial` (a limit was reached, the part that fit is searchable), `error` (hover for the reason).
- **Upload**: choose the collection, choose files, *Upload*. **Add URL**: AIFRED downloads the page or document.
- **Formats**: text, Markdown, HTML, JSON, CSV, YAML; PDF; Word, Excel, PowerPoint and OpenDocument files, including the old `.doc`, `.xls` and `.ppt` (text only), e-mail messages (`.eml`) and EPUB books; JPG, PNG and WebP images (read by the vision model). `RAG_FORMATS` chooses which are accepted. The size limit is `RAG_MAX_DOCUMENT_BYTES` (5 MB by default).

### Test search
Type a question, choose the mode and press `Enter`.

![A test search showing how each result was found](/static/guide/img/knowledge-2-search.png)

- **hybrid** (default) combines meaning (dense) and keywords; **dense** only meaning; **keyword** only words.
- Each result shows the collection, the file and heading, and badges: `dense #1`, `keyword #2` are the ranks it had in each list; `rerank 0.93` appears when a rerank model re-ordered the candidates; `flagged` means the document tripped the injection scanner; `machine-read` means the text came from a vision model, the OCR engine or a transcription.

### Retrieval quality
Measure whether search finds the right passage, before and after a change.

![Golden questions and evaluation](/static/guide/img/knowledge-3-quality.png)

- **Golden questions**: questions plus where the answer lives: a document (name or part of it), a phrase the passage must contain, or both. Add them by hand, or press **Write questions from my documents**: the model reads a few random passages and writes one question each (the passages are sent to the provider of the `assist` route); keep the good ones with *Keep*.
- **Run evaluation**: pick how many results count (top 1, 3, 5, 10), give the run a label ("before rerank") and press the button. Every question is searched four ways: `hybrid`, `dense`, `keyword` and `hybrid+rerank` (only if a rerank model is configured).
- **Recall** is the share of questions whose right passage was among the results; **MRR** is higher the nearer the top it was. The table lists the questions that were missed. *Earlier runs* shows ▲ and ▼ against the previous run of the same search, so you can see whether a change helped.

## How to

- **Add documents**: *Upload* or *Add URL*, or put files in `workspace/knowledge/<collection>/` inside the data directory and press *Scan folder* (new and changed files are queued, deleted files removed).
- **Use a rerank model**: set `RAG_RERANK_PROVIDER` and `RAG_RERANK_MODEL` (a provider that offers a `/rerank` endpoint: Jina, Cohere, Voyage, vLLM). Then compare with *Run evaluation*.
- **Change the embedding model**: change it in Config, then *Re-index*; meanwhile searches still find passages by keywords.

- **Let the open bot read a collection**: tick *Open bot* on it, and add `knowledge` to `TOOLSETS_TELEGRAM` on the Config page. Only ticked collections are listed or searched for that bot; everything else answers as if it did not exist.
- **Index changes at once**: turn on `RAG_WATCH`. A file you save, move or delete under `workspace/knowledge/<collection>/` is indexed a couple of seconds later (Linux only; `RAG_SCAN_MINUTES` remains the fallback).
- **Read text in images on this machine**: set `RAG_OCR_ENGINE` to `tesseract` (always local, free, no limit, text only) or `auto` (tesseract first, the vision model when it finds too little). The Docker image includes tesseract with English and Italian (`RAG_OCR_LANGS`).
- **Index recordings**: add `audio` to `RAG_FORMATS`, choose `RAG_AUDIO_PROVIDER` and `RAG_AUDIO_MODEL` (an OpenAI-compatible `/audio/transcriptions` endpoint: OpenAI, Groq, Mistral, a local Whisper server). MP3, WAV, M4A, Ogg, FLAC and WebM are read; each passage keeps its time (`1:05`). The sound is sent to that provider, so choose a local one if it matters.
- **Very large collections**: from `RAG_ANN_MIN_CHUNKS` passages (500,000) an approximate index is built in the background and kept up to date; below that, search is exact. Raise `RAG_ANN_PROBE` for better recall.

## Good to know

- The assistant only **reads** the knowledge base; it never writes to it. Passages are marked untrusted: instructions inside a document are not followed.
- Every file is opened by a separate, limited process (no network, no secrets, time and memory limits).
- The open Telegram bot never sees your documents.
