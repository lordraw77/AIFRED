// Knowledge: collections, documents with state, upload, URLs, scan, re-index and a test search that shows how each hit was found (spec 9.4).
import { ago, attempt, badge, bytes, button, busy, clear, dangerButton, del, disclosure, empty, field, get, h, number, panel, post, put, skeleton, state, toast } from "../core.js";

const STATUS_KIND = { ready: "ok", queued: "", indexing: "warn", waiting: "warn", partial: "warn", error: "err" };
const ACCEPT = ".md,.txt,.rst,.html,.json,.csv,.yaml,.yml,.pdf,.docx,.xlsx,.pptx,.odt,.ods,.odp,.jpg,.jpeg,.png,.webp";

export default {
  async render(root, ctx) {
    const status = await get("/api/rag/status");
    if (!status.enabled) {
      root.append(panel("Knowledge base", "knowledge", h("div", { class: "notice warn" }, "The knowledge base is off. Turn on RAG_ENABLED and choose the embedding provider and model on the Config page."),
        h("div", { class: "row pad-t" }, button("Open Config", () => { location.hash = "#/config"; }, { primary: true, small: true }))));
      ctx.setCount(null);
      return;
    }
    let collection = "";
    let timer = null;
    const body = h("div", {});
    root.append(body);

    async function draw() {
      const [st, collections, docs] = await Promise.all([get("/api/rag/status"), get("/api/rag/collections"), get(`/api/rag/documents${collection ? `?collection=${encodeURIComponent(collection)}` : ""}`)]);
      ctx.setCount(st.documents ? Object.values(st.documents).reduce((a, b) => a + b, 0) : 0);
      clear(body);
      body.append(statusPanel(st), collectionsPanel(collections), documentsPanel(collections, docs), searchPanel(collections), qualityPanel(collections, docs));
      clearTimeout(timer);
      if (docs.some((d) => ["queued", "indexing", "waiting"].includes(d.status))) timer = setTimeout(draw, 3000);
    }

    function statusPanel(st) {
      const e = st.embedding;
      return panel("Status", "knowledge",
        h("div", { class: "stats" },
          h("div", { class: "stat" }, h("div", { class: "value" }, String(st.collections)), h("div", { class: "label" }, "Collections")),
          h("div", { class: "stat" }, h("div", { class: "value" }, number(st.chunks)), h("div", { class: "label" }, "Passages")),
          h("div", { class: "stat" }, h("div", { class: "value" }, bytes(st.cache_bytes)), h("div", { class: "label" }, "Vector cache")),
          Object.entries(st.documents || {}).map(([k, v]) => h("div", { class: "stat" }, h("div", { class: "value" }, String(v)), h("div", { class: "label" }, k)))),
        visionBlock(st.vision),
        h("p", { class: "hint" }, e.model ? `Embedding model: ${e.provider} / ${e.model}. The text of your documents is sent to that provider to be embedded.` : "No embedding model chosen: search is keyword-only. Set RAG_EMBED_PROVIDER and RAG_EMBED_MODEL on the Config page."));
    }

    function visionBlock(v) {
      const pct = v.daily_limit ? Math.min(100, Math.round((v.used_today / v.daily_limit) * 100)) : 0;
      const bar = h("div", { class: "bar", role: "img", "aria-label": `${pct}% of today's limit` }, h("span", {}));
      bar.firstChild.style.setProperty("--pct", String(pct));
      return h("div", { class: "stack" },
        h("div", { class: "row between" }, h("span", { class: "label" }, `Vision route: ${v.route}`),
          h("span", { class: "mono" }, v.daily_limit ? `${v.used_today} / ${v.daily_limit} images today` : "vision is off (RAG_VISION_DAILY_LIMIT is 0)")),
        v.daily_limit ? bar : null,
        h("p", { class: "hint" }, v.models.length ? `Images and scanned pages are sent to: ${v.models.join(", ")} (first answers; the others are fallbacks).` : `No vision model yet: images and scanned pages cannot be read. Put a model that passed Test vision on the ${v.route} route (Models page). Nothing is sent anywhere until you do.`));
    }

    function collectionsPanel(collections) {
      const name = h("input", { type: "text", placeholder: "collection name", pattern: "[a-z0-9][a-z0-9_\\-]{0,31}" });
      const desc = h("input", { type: "text", placeholder: "what is in it" });
      const rows = collections.map((c) => h("tr", {},
        h("td", { class: "mono" }, c.name, c.stale > 0 && badge("re-index needed", "warn")), h("td", {}, c.description || "–"), h("td", { class: "mono" }, `${c.documents} docs · ${number(c.chunk_count)} passages`),
        h("td", { class: "mono" }, c.embedding.model ? `${c.embedding.provider} / ${c.embedding.model}` : "keyword only"),
        h("td", {}, c.pending > 0 && badge(`${c.pending} indexing`, "warn"), c.errors > 0 && badge(`${c.errors} failed`, "err")),
        h("td", { class: "row" }, button("Re-index", async () => { const r = await attempt(() => post("/api/rag/reindex", { collection: c.name })); if (r) { toast(`${r.queued} documents queued`, "ok"); draw(); } }, { small: true, iconName: "refresh" }),
          dangerButton("Delete", async () => { if (await attempt(() => del(`/api/rag/collections/${encodeURIComponent(c.name)}`), "Collection deleted")) { if (collection === c.name) collection = ""; draw(); } }))));
      return panel("Collections", "knowledge",
        collections.length ? h("table", {}, h("thead", {}, h("tr", {}, ["Name", "Description", "Size", "Embedding", "State", ""].map((x) => h("th", {}, x)))), h("tbody", {}, rows)) : empty("No collection yet. Create one below, then add documents."),
        h("div", { class: "row pad-t" }, name, desc, button("Create", async () => { if (name.value.trim() && await attempt(() => post("/api/rag/collections", { name: name.value.trim(), description: desc.value.trim() }), "Collection created")) draw(); }, { primary: true, small: true, iconName: "plus" }),
          button("Scan folder", async () => { const r = await attempt(() => post("/api/rag/scan", {})); if (r) { toast(`Scan: ${r.queued} queued, ${r.unchanged} unchanged, ${r.removed} removed`, "ok"); draw(); } }, { small: true, iconName: "refresh" })));
    }

    function documentsPanel(collections, docs) {
      const pick = h("select", { "aria-label": "Collection", onchange: (e) => { collection = e.target.value; draw(); } }, h("option", { value: "" }, "All collections"), collections.map((c) => h("option", { value: c.name, selected: c.name === collection }, c.name)));
      const target = h("select", { "aria-label": "Add to" }, collections.map((c) => h("option", { value: c.name, selected: c.name === (collection || collections[0]?.name) }, c.name)));
      const file = h("input", { type: "file", multiple: true, "aria-label": "Files to upload", accept: ACCEPT });
      const url = h("input", { type: "url", placeholder: "https://… page or document to download" });
      const upload = button("Upload", null, { primary: true, small: true });
      upload.onclick = busy(upload, async () => {
        for (const f of file.files) {
          const resp = await fetch(`/api/rag/documents?collection=${encodeURIComponent(target.value)}&name=${encodeURIComponent(f.name)}`, {
            method: "POST", credentials: "same-origin", headers: { "X-CSRF-Token": state.csrf, "Content-Type": "application/octet-stream" }, body: f });
          if (resp.status === 401) { state.onUnauthorized(); return; }
          const out = await resp.json().catch(() => ({}));
          toast(resp.ok ? `${f.name}: queued` : `${f.name}: ${typeof out.detail === "string" ? out.detail : resp.status}`, resp.ok ? "ok" : "err");
        }
        file.value = ""; draw();
      });
      const rows = docs.map((d) => h("tr", {},
        h("td", { class: "row" }, d.kind === "image" ? h("img", { class: "thumb", src: `/api/rag/documents/${d.id}/file`, alt: `Thumbnail of ${d.source}`, loading: "lazy" }) : null,
          h("div", {}, h("div", {}, d.title || d.source), h("div", { class: "hint mono" }, `${d.collection} · ${d.source}`), d.error && h("div", { class: d.status === "error" ? "hint over" : "hint" }, d.error))),
        h("td", {}, badge(d.status, STATUS_KIND[d.status] || ""), d.flagged ? badge("flagged", "warn") : null), h("td", { class: "mono" }, d.kind), h("td", { class: "mono" }, String(d.chunks)), h("td", { class: "mono" }, bytes(d.size)), h("td", {}, ago(d.updated)),
        h("td", {}, dangerButton("Delete", async () => { if (await attempt(() => del(`/api/rag/documents/${d.id}`), "Document deleted")) draw(); }))));
      return panel("Documents", "memory",
        h("div", { class: "row" }, pick),
        docs.length ? h("table", {}, h("thead", {}, h("tr", {}, ["Document", "State", "Kind", "Passages", "Size", "Updated", ""].map((x) => h("th", {}, x)))), h("tbody", {}, rows)) : empty("No document here yet."),
        collections.length ? h("div", { class: "stack pad-t" }, h("div", { class: "row" }, target, file, upload), h("div", { class: "row" }, url, button("Add URL", async () => {
          if (url.value.trim() && await attempt(() => post("/api/rag/documents/url", { collection: target.value, url: url.value.trim() }), "URL queued")) { url.value = ""; draw(); }
        }, { small: true }))) : null);
    }

    function searchPanel(collections) {
      const q = h("input", { type: "text", placeholder: "Test a search" });
      const mode = h("select", {}, ["hybrid", "dense", "keyword"].map((m) => h("option", { value: m }, m)));
      const out = h("div", {});
      const run = async () => {
        if (!q.value.trim()) return;
        clear(out);
        const r = await attempt(() => post("/api/rag/search", { query: q.value.trim(), mode: mode.value, collection: collection || null }));
        if (!r) return;
        for (const n of r.notes) out.append(h("div", { class: "notice warn" }, n));
        if (!r.hits.length) out.append(empty("No matching passage."));
        for (const hit of r.hits) {
          out.append(h("div", { class: "item" }, h("div", { class: "row" }, h("span", { class: "mono" }, `${hit.collection} / ${hit.source}${hit.heading ? ` › ${hit.heading}` : ""}${hit.page ? ` p. ${hit.page}` : ""}`),
            hit.dense_rank ? badge(`dense #${hit.dense_rank}`) : null, hit.keyword_rank ? badge(`keyword #${hit.keyword_rank}`) : null, hit.rerank !== null && hit.rerank !== undefined ? badge(`rerank ${hit.rerank.toFixed(2)}`, "ok") : null, hit.flagged ? badge("flagged", "warn") : null, hit.machine_read ? badge("machine-read") : null),
            h("div", { class: "pre" }, hit.text)));
        }
      };
      q.addEventListener("keydown", (e) => { if (e.key === "Enter") run(); });
      return panel("Test search", "logs", h("div", { class: "row" }, q, mode, button("Search", run, { primary: true, small: true })), out);
    }

    // Retrieval quality: golden questions and evaluation runs ------------------------------------------

    function qualityPanel(collections, docs) {
      const box = h("div", { class: "stack" });
      const list = h("div", {}), results = h("div", {}), history = h("div", {}), suggestions = h("div", {});
      const openRuns = new Set();   // evaluation rows whose details are open, kept across redraws
      const question = h("input", { type: "text", placeholder: "A question the documents should answer", maxlength: "300", "aria-label": "Question" });
      const source = h("input", { type: "text", placeholder: "Which document (name or part of it)", maxlength: "300", list: "golden-sources", "aria-label": "Expected document" });
      const phrase = h("input", { type: "text", placeholder: "Optional: a phrase the passage must contain", maxlength: "200", "aria-label": "Expected phrase" });
      const sources = h("datalist", { id: "golden-sources" }, [...new Set(docs.map((d) => d.source))].map((x) => h("option", { value: x })));
      const k = h("select", { "aria-label": "Results counted" }, [1, 3, 5, 10].map((n) => h("option", { value: String(n), selected: n === 5 || null }, `top ${n}`)));
      const label = h("input", { type: "text", placeholder: "Label for this run, e.g. before rerank", maxlength: "60", "aria-label": "Run label" });

      const pct = (x) => `${Math.round(x * 100)}%`;
      const loadList = async () => {
        const rows = await attempt(() => get("/api/rag/golden")) || [];
        clear(list);
        if (!rows.length) list.append(empty("No golden questions yet. Add a few, or let the model write some from your documents."));
        for (const g of rows) {
          list.append(h("div", { class: "item" }, h("div", { class: "row between" }, h("span", {}, g.question),
            dangerButton("Delete", async () => { if (await attempt(() => del(`/api/rag/golden/${g.id}`), "Deleted")) loadList(); })),
            h("div", { class: "mono muted" }, [g.source && `in ${g.source}`, g.phrase && `containing "${g.phrase}"`].filter(Boolean).join(" · "))));
        }
      };
      const loadHistory = async () => {
        const rows = await attempt(() => get("/api/rag/evals?limit=40")) || [];
        clear(history);
        if (!rows.length) return;
        const rank = (r, i) => { const before = rows.slice(i + 1).find((x) => x.variant === r.variant && x.k === r.k); return before ? r.recall - before.recall : null; };
        history.append(h("div", { class: "label" }, "Earlier runs"), ...rows.slice(0, 16).map((r, i) => {
          const d = rank(r, i);
          const key = `${r.ts}:${r.variant}:${r.k}`;
          return disclosure(key, openRuns,
            [h("span", { class: "muted" }, ago(r.ts)), h("span", {}, r.label || "–"), h("span", { class: "mono" }, r.variant), h("span", { class: "mono" }, `top ${r.k}`),
              h("span", { class: "mono" }, pct(r.recall), d === null || d === 0 ? "" : h("span", { class: d > 0 ? "good" : "bad" }, ` ${d > 0 ? "▲" : "▼"}${Math.abs(Math.round(d * 100))}`)), h("span", { class: "mono" }, `MRR ${r.mrr.toFixed(2)}`)],
            () => [h("div", { class: "hint" }, `${r.total} questions`),
              r.misses.length ? h("div", {}, h("span", { class: "label" }, "Missed"), h("div", { class: "pre" }, r.misses.join("\n"))) : h("div", { class: "hint" }, "Nothing missed.")]);
        }));
      };
      const add = button("Add question", async () => {
        if (!question.value.trim()) return;
        if (await attempt(() => post("/api/rag/golden", { question: question.value, source: source.value, phrase: phrase.value, collection: collection || "" }), "Question added")) { question.value = ""; phrase.value = ""; loadList(); }
      }, { small: true, primary: true });
      const suggest = button("Write questions from my documents", null, { small: true, title: "The model reads a few random passages and writes a question for each" });
      suggest.onclick = busy(suggest, async () => {
        const r = await attempt(() => post(`/api/rag/golden/suggest?count=6${collection ? `&collection=${encodeURIComponent(collection)}` : ""}`));
        clear(suggestions);
        if (!r) return;
        suggestions.append(h("div", { class: "hint" }, "Written by the model of the assist route (the passages were sent there). Keep the ones that are good tests."));
        for (const q of r) {
          const item = h("div", { class: "item" }, h("div", {}, q.question), h("div", { class: "mono muted" }, `in ${q.source}`));
          item.append(h("div", { class: "row" },
            button("Keep", async () => { if (await attempt(() => post("/api/rag/golden", q), "Question added")) { item.remove(); loadList(); } }, { small: true, primary: true }),
            button("Skip", () => item.remove(), { small: true })));
          suggestions.append(item);
        }
      });
      const run = button("Run evaluation", null, { small: true, primary: true, title: "Search every question each way and score where the right passage ranks" });
      run.onclick = busy(run, async () => {
        clear(results);
        const r = await attempt(() => post("/api/rag/evaluate", { k: Number(k.value), label: label.value }));
        if (!r) return;
        results.append(h("table", {}, h("thead", {}, h("tr", {}, ["Search", "Recall", "MRR", "Missed"].map((x) => h("th", {}, x)))),
          h("tbody", {}, r.results.map((x) => h("tr", {}, h("td", { class: "mono" }, x.variant), h("td", { class: "mono" }, pct(x.recall)), h("td", { class: "mono" }, x.mrr.toFixed(2)),
            h("td", {}, x.misses.length ? x.misses.join(" · ") : "none"))))),
          h("div", { class: "hint" }, r.reranking ? "hybrid+rerank is the hybrid search with the rerank model re-ordering the best candidates." : "Set RAG_RERANK_PROVIDER and RAG_RERANK_MODEL on the Config page to compare reranking."));
        loadHistory();
      });
      box.append(
        h("p", { class: "hint" }, "A golden question says where its answer lives. Recall is the share of questions whose right passage is among the top results; MRR is higher when it sits nearer the top. Run it before and after a change (chunk size, embedding model, rerank) to see whether search got better."),
        list, h("div", { class: "row" }, question, source, phrase, add, sources), h("div", { class: "row" }, suggest), suggestions,
        h("div", { class: "row" }, k, label, run), results, history);
      loadList(); loadHistory();
      return panel("Retrieval quality", "logs", box);
    }

    await draw();
    return () => clearTimeout(timer);
  },
};
