# Questa guida

La guida che stai leggendo fa parte della dashboard, quindi corrisponde sempre alla versione che esegui.

![La pagina della guida](/static/guide/img/guide-1.png)

## Come funziona

- **L'elenco a sinistra** ha una sezione per ogni pagina della dashboard, più *Primi passi* e *Comandi di chat e Telegram*.
- **English / Italiano** in cima all'elenco cambia la lingua; la scelta viene ricordata. La prima volta la guida si apre nella lingua del tuo browser.
- **Il pulsante *Guide*** in alto a destra di ogni pagina apre la sezione che la riguarda.
- **Previous / Next** (Precedente / Successiva) in fondo a una sezione scorrono la guida in ordine.
- Le immagini si aprono a grandezza naturale in una nuova scheda con un clic.
- Le immagini mostrano la dashboard in inglese; il testo italiano descrive gli stessi pulsanti con i loro nomi inglesi.

## Per chi sviluppa

La guida è fatta di file Markdown in `aifred/web/static/guide/` (`en/` e `it/`, un file per sezione, elencati in `index.json`) e di immagini in `img/`. Le immagini sono prese da un'istanza dimostrativa con dati inventati dagli script in `scripts/guide-screenshots/`, così si possono rifare ogni volta che la dashboard cambia.
