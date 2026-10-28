# Config

Tutte le impostazioni di AIFRED in una pagina, raggruppate per argomento. Le modifiche valgono subito, senza riavvio. Provider, server MCP e le loro chiavi stanno nelle rispettive pagine; tutto il resto è qui.

![Filtrare le impostazioni](/static/guide/img/config-1-filter.png)

## Cosa vedi

- **La casella del filtro** (il tasto `/` ci porta): mostra solo le impostazioni il cui nome, descrizione o gruppo contengono le tue parole.
- **Un pannello per gruppo**, ogni riga con il **nome**, una **descrizione**, il **controllo** e, quando il valore differisce da quello predefinito, `default: …` sotto.
- **Controlli**: una casella per le impostazioni on/off, un menu per le scelte, una casella numerica, una casella di testo (le liste si separano con virgole), una casella JSON.
- **I segreti** (come `TELEGRAM_TOKEN`) sono **solo in scrittura**: la casella dice *set; type to replace* oppure *not set*, il valore non torna mai al browser e *Clear* lo rimuove.
- **La barra di salvataggio** compare in basso con il numero di modifiche non salvate: **Save** le applica tutte insieme (se un valore non è valido non si salva nulla e ti viene detto quale), **Discard** le annulla. Filtrare con modifiche non salvate è bloccato, così non se ne perde nessuna.

## Le impostazioni

### llm
| Impostazione | Predefinito | Significato |
|---|---|---|
| `LLM_THINKING` | off | Modalità di ragionamento per i modelli che la supportano; `/think` la cambia per una conversazione. |
| `LLM_TEMPERATURE` | 0.6 | Quanto è azzardato il linguaggio (da 0 a 2). |
| `LLM_EXTRA_BODY` | {} | Un oggetto JSON aggiunto a ogni richiesta, per opzioni specifiche del provider. |

### routing
| Impostazione | Predefinito | Significato |
|---|---|---|
| `ROUTE_CLI`, `ROUTE_TELEGRAM`, `ROUTE_API`, `ROUTE_WEB` | default | La route usata da ogni gateway. |
| `ROUTE_CRON` | default | La route delle attività pianificate. |
| `ROUTE_COMPRESS` | vuoto | La route che scrive i riassunti delle conversazioni (vuoto: quella della conversazione; basta un modello più economico). |
| `ROUTE_DEADLINE` | 120 | Secondi concessi per percorrere una route. |
| `COOLDOWN_QUOTA` | 3600 | Secondi di riposo di un modello dopo che la sua quota è finita. |

### agent
| Impostazione | Predefinito | Significato |
|---|---|---|
| `MAX_ITERATIONS` | 12 | Massimo di giri di strumenti per richiesta. |
| `HISTORY_LIMIT` | 40 | Messaggi di cronologia inviati al modello. |
| `MEMORY_LIMIT` | 2200 | Caratteri che le note dell'assistente (`MEMORY.md`) possono occupare; vanno con ogni richiesta. |
| `USER_LIMIT` | 1375 | Caratteri che il profilo utente (`USER.md`) può occupare; vanno con ogni richiesta. |
| `COMPRESS_ENABLED` | on | Riassumere la parte più vecchia di una conversazione lunga. |
| `COMPRESS_TRIGGER_TOKENS` | 12000 | Dimensione stimata che fa scattare un riassunto. |
| `COMPRESS_KEEP_MESSAGES` | 8 | Ultimi messaggi tenuti parola per parola. |
| `COMPRESS_SUMMARY_TOKENS` | 700 | Limite di lunghezza di un riassunto. |
| `IDLE_MINUTES` | 30 | Dopo tanto silenzio il prompt dell'assistente viene ricostruito. |
| `TOOL_OUTPUT_LIMIT` | 8000 | Massimo di caratteri del risultato di uno strumento passato al modello. |
| `INSTRUCTIONS_BUDGET` | 3000 | Massimo di caratteri della sezione *Capabilities*. |
| `ASSIST_EVALUATIONS_PER_HOUR` | 10 | Prove di istruzioni che la dashboard può fare in un'ora. |

### tools
| Impostazione | Predefinito | Significato |
|---|---|---|
| `TOOLSETS` | memory, search, skills, web, file | I toolset che ogni gateway ha, a meno che non abbia un elenco proprio. |
| `TOOLSETS_CLI`, `TOOLSETS_TELEGRAM`, `TOOLSETS_API`, `TOOLSETS_WEB` | vuoto (eredita) | L'elenco proprio di un gateway. Gli interruttori di [Tools](#/guide?s=tools) e [MCP](#/guide?s=mcp) li modificano per te. |
| `TOOLSETS_CRON` | search, web, skills | Toolset delle attività pianificate; aggiungine altri solo per attività di cui ti fidi. |
| `ENABLE_TERMINAL` | off | Scorciatoia che aggiunge `terminal` (comandi di shell). |
| `TERMINAL_TIMEOUT` | 60 | Secondi che un comando può durare. |
| `WEB_ALLOW_PRIVATE` | off | Permette al lettore di pagine di raggiungere indirizzi della tua rete. |
| `SEARXNG_URL` | vuoto | Indirizzo di un server SearXNG; attiva la ricerca sul web. |

### telegram
| Impostazione | Predefinito | Significato |
|---|---|---|
| `TELEGRAM_TOKEN` | non impostato | Il token del bot; attiva il gateway Telegram. |
| `TELEGRAM_ALLOWED_IDS` | vuoto | Id numerici autorizzati a parlare col bot. **Vuoto significa aperto a tutti** (modalità ristretta). |
| `TELEGRAM_API_BASE` | api.telegram.org | Cambialo solo per usare un server Bot API locale. |

### scheduler
| Impostazione | Predefinito | Significato |
|---|---|---|
| `SCHEDULER_ENABLED` | on | Esegue le attività pianificate. |
| `SCHEDULER_TIMEZONE` | vuoto | Fuso orario delle pianificazioni, es. `Europe/Rome` (vuoto: quello del server). |
| `SCHEDULER_MIN_INTERVAL` | 5 | Intervallo minimo, in minuti, tra le esecuzioni di una attività. |
| `SCHEDULER_MAX_TASKS` | 20 | Numero massimo di attività. |
| `SCHEDULER_RUN_TIMEOUT` | 600 | Secondi che una esecuzione può durare. |
| `SCHEDULER_MAX_PARALLEL` | 2 | Esecuzioni contemporanee. |

### agents
| Impostazione | Predefinito | Significato |
|---|---|---|
| `AGENT_MAX_ITERATIONS` | 8 | Giri di strumenti di un sub-agente (se non ha un limite proprio). |
| `AGENT_TIMEOUT` | 300 | Secondi di una esecuzione di sub-agente. |
| `AGENT_MAX_PARALLEL` | 3 | Sub-agenti contemporanei in una delega. |
| `AGENT_MAX_AGENTS` | 20 | Numero massimo di sub-agenti. |

### mcp
| Impostazione | Predefinito | Significato |
|---|---|---|
| `MCP_CONNECT_TIMEOUT`, `MCP_CALL_TIMEOUT` | 15, 120 | Timeout predefiniti in secondi. |
| `MCP_MAX_TOOLS_PER_SERVER` | 40 | Massimo di strumenti presi da un server. |
| `MCP_STDIO_COMMANDS` | vuoto | Nomi dei programmi che la dashboard può avviare come server MCP. Vuoto: la funzione è spenta. |
| `MCP_OAUTH_CLIENT_ID` | vuoto | Client id registrato a mano per l'accesso OAuth. |
| `MCP_OAUTH_PUBLIC_URL` | vuoto | Indirizzo https pubblico di AIFRED, perché possa servire il suo documento di metadati del client. |

### knowledge
| Impostazione | Predefinito | Significato |
|---|---|---|
| `RAG_ENABLED` | off | Attiva la knowledge base. |
| `RAG_EMBED_PROVIDER`, `RAG_EMBED_MODEL` | vuoto | Il modello di embedding (vuoto: solo ricerca per parole chiave). Sostituibile per collezione. |
| `RAG_RERANK_PROVIDER`, `RAG_RERANK_MODEL` | vuoto | Un modello di rerank facoltativo su un provider con endpoint `/rerank`. |
| `RAG_RERANK_CANDIDATES` | 20 | Candidati che il modello di rerank riordina. |
| `RAG_CHUNK_CHARS`, `RAG_CHUNK_OVERLAP` | 1200, 150 | Dimensione di ogni passaggio e sovrapposizione tra vicini. Cambiarli richiede *Re-index*. |
| `RAG_EMBED_BATCH` | 16 | Passaggi per richiesta di embedding. |
| `RAG_TOP_K` | 5 | Risultati restituiti di default (da 1 a 10). |
| `RAG_MIN_SIMILARITY` | 0.30 | Le corrispondenze di significato sotto questa soglia sono scartate. |
| `RAG_MAX_DOCUMENT_BYTES` | 5 MB | Documento più grande. |
| `RAG_MAX_CHUNKS` | 100000 | Massimo di passaggi in tutta la base. |
| `RAG_CACHE_MB` | 512 | Memoria per la ricerca veloce (0: legge ogni volta il database). |
| `RAG_SCAN_MINUTES` | 0 | Riesamina la cartella knowledge ogni N minuti (0: solo su richiesta). |
| `RAG_FORMATS` | text, pdf, office, image | Formati accettati. |
| `RAG_VISION_ROUTE` | vision | La route il cui modello legge immagini e pagine scansionate. |
| `RAG_VISION_DAILY_LIMIT` | 200 | Immagini e pagine scansionate inviate al modello vision al giorno (0: vision spenta). |
| `RAG_MAX_IMAGE_BYTES` | 10 MB | Immagine più grande. |
| `RAG_MAX_PAGES` | 500 | Pagine lette da un PDF o da una presentazione. |
| `RAG_OCR_MIN_CHARS` | 40 | Una pagina PDF con meno caratteri è trattata come scansione. |
| `RAG_RENDER_MAX_SIDE` | 1600 | Lato più lungo in pixel di una pagina scansionata inviata al modello vision. |
| `RAG_PARSE_TIMEOUT`, `RAG_PARSE_MEMORY_MB` | 60, 768 | Limiti del processo isolato che apre ogni file. |

### review
| Impostazione | Predefinito | Significato |
|---|---|---|
| `REVIEW_ENABLED` | on | Dopo alcune conversazioni l'assistente le rivede in background e salva ciò che vale la pena tenere. |
| `REVIEW_EVERY_TURNS` | 5 | Minimo di scambi tra una revisione e l'altra. |
| `REVIEW_MAX_INPUT_TOKENS` | 6000 | Tetto di costo di una revisione. |
| `WRITE_APPROVAL` | off | `off`, `memory`, `skills` o `all`: quali salvataggi aspettano la tua approvazione in [Journey](#/guide?s=journey). |
| `NOTIFICATIONS` | on | `off`, `on` o `verbose`: i piccoli messaggi su salvataggi e ripieghi. |

### system
| Impostazione | Predefinito | Significato |
|---|---|---|
| `LOG_LEVEL` | INFO | Quanto scrive il server nei [Logs](#/guide?s=logs). |

## Da sapere

- Le impostazioni stanno nel database, non in `.env`. Il file `.env` porta solo il primo account amministratore e le opzioni dell'immagine (cartella dei dati, indirizzo di ascolto).
- Per spostare le impostazioni tra macchine esistono l'esportazione e l'importazione tramite API (`/api/config/export`), senza i segreti.
