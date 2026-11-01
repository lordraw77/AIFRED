// Facts: long-term memory. What the assistant keeps between conversations, whose it is, how it is found, and how to
// export, import, pause or forget it. The settings that govern it sit on this page too.
import { ago, attempt, badge, busy, button, clear, dangerButton, del, empty, field, get, h, panel, post, put, skeleton, toast } from "../core.js";

const KINDS = ["preference", "decision", "fact", "outcome"];
const SETTINGS = [
  ["AUTO_MEMORY", "enum", "Memory", "off: nothing is stored. owner: only your own chats. all: also other people's chats and the listed sub-agents."],
  ["MEMORY_EXTRACT_ROUTE", "route", "Route that reads conversations", "Empty: the conversation's own route. The text of conversations is sent to it."],
  ["MEMORY_EXTRACT_FALLBACK", "bool", "Fall back to the conversation's route", "When the route above is unavailable."],
  ["MEMORY_EVERY_TURNS", "int", "Read every N new turns", "A conversation is also read when it ends or goes idle."],
  ["MEMORY_IDLE_MINUTES", "int", "Idle minutes before a conversation counts as ended", ""],
  ["MEMORY_MAX_FACTS_PER_RUN", "int", "Most facts per reading", ""],
  ["MEMORY_AGENTS", "text", "Sub-agents with their own memory", "Names separated by commas; used when memory is on for all."],
  ["MEMORY_RECALL_K", "int", "Most facts added to a request", ""],
  ["MEMORY_RECALL_TOKENS", "int", "Length limit of the recalled facts (tokens)", ""],
  ["MEMORY_RETENTION_DAYS", "int", "Archive facts not recalled for (days)", "0 keeps everything."],
  ["MEMORY_GRACE_DAYS", "int", "Delete archived facts after (days)", "Until then they can be restored."],
];

const when = (t) => new Date(t * 1000).toLocaleDateString();

export default {
  async render(root, ctx) {
    root.append(skeleton(5));
    let status = await get("/api/ltm"), config = await get("/api/config"), routes = await get("/api/routes").catch(() => []);
    const filter = { subject: "", state: "active", kind: "", q: "" };
    let facts = [], openSettings = false;
    const view = h("div", {});
    clear(root); root.append(view);

    const reload = async () => { status = await get("/api/ltm"); await loadFacts(); };
    async function loadFacts() {
      const p = new URLSearchParams({ status: filter.state, limit: "200" });
      for (const k of ["subject", "kind", "q"]) if (filter[k]) p.set(k, filter[k]);
      facts = (await get(`/api/ltm/facts?${p}`)).facts;
      draw();
    }

    // Status and settings ----------------------------------------------------------------------------
    function statusPanel() {
      const last = status.last_extraction;
      const lines = [
        h("div", { class: "row wrap" }, badge(status.mode === "off" ? "off" : `on: ${status.mode}`, status.mode === "off" ? "" : "ok"),
          h("span", { class: "mono" }, `${status.total} fact${status.total === 1 ? "" : "s"}`),
          ...Object.entries(status.by_status).map(([k, n]) => h("span", { class: "muted" }, `${n} ${k}`))),
        h("p", { class: "muted small" }, `Reads conversations with ${status.extract_route ? `route "${status.extract_route}"` : "the conversation's own route"}${status.fallback ? " (falls back to it)" : ""}. `
          + `Finds facts by ${status.embedding ? `meaning (${status.embedding}) and keywords` : "keywords only (set an embedding model to add meaning)"}. `
          + `Archives unused facts after ${status.retention_days || "∞"} days and deletes them ${status.grace_days} days later.`),
        last ? h("p", { class: "muted small" }, `Last reading ${ago(last.at)}: ${last.facts ?? 0} new, ${last.merged ?? 0} merged, ${last.replaced ?? 0} replaced, ${last.rejected ?? 0} refused${last.route ? ` · ${last.route}` : ""}.`)
          : h("p", { class: "muted small" }, "No conversation has been read yet."),
      ];
      const upkeep = button("Run upkeep now", async () => { const r = await attempt(() => post("/api/ltm/maintain")); if (r) { toast(`Archived ${r.archived}, deleted ${r.purged}, merged ${r.merged}`, "ok"); await reload(); } }, { small: true });
      return panel("Long-term memory", "memory", ...lines, h("div", { class: "row" }, button(openSettings ? "Hide settings" : "Settings", () => { openSettings = !openSettings; draw(); }, { small: true, primary: !openSettings }), upkeep));
    }

    function settingsPanel() {
      const byKey = new Map(config.map((c) => [c.key, c]));
      const inputs = new Map();
      const rows = SETTINGS.map(([key, kind, label, hint]) => {
        const item = byKey.get(key);
        let node, read;
        if (kind === "enum") { node = h("select", {}, item.choices.map((c) => h("option", { value: c }, c))); node.value = item.value; read = () => node.value; }
        else if (kind === "route") { node = h("select", {}, h("option", { value: "" }, "(the conversation's own route)"), ...routes.map((r) => h("option", { value: r.name }, r.name))); node.value = item.value || ""; read = () => node.value; }
        else if (kind === "bool") { node = h("input", { type: "checkbox" }); node.checked = item.value === true; read = () => String(node.checked); }
        else if (kind === "text") { node = h("input", { type: "text" }); node.value = (item.value || []).join(", "); read = () => node.value; }
        else { node = h("input", { type: "number", min: "0" }); node.value = item.value; read = () => node.value; }
        inputs.set(key, read);
        return field(label, kind === "bool" ? h("label", { class: "check" }, node, h("span", {}, "on")) : node, hint || null);
      });
      const save = button("Save", null, { primary: true, small: true });
      save.onclick = busy(save, async () => {
        const values = {};
        for (const [key, read] of inputs) values[key] = read();
        const fresh = await attempt(() => put("/api/config", { values }), "Saved. Changes apply now.");
        if (fresh) { config = fresh; await reload(); }
      });
      return panel("Settings", "config", h("div", { class: "stack" }, ...rows, h("div", { class: "row" }, save,
        h("a", { href: "#/config" }, "More in Config (group memory)"))));
    }

    // Facts --------------------------------------------------------------------------------------------
    function factRow(f) {
      const body = h("div", { class: "grow" });
      const show = () => {
        clear(body);
        body.append(h("div", {}, f.text),
          h("div", { class: "row wrap muted small" }, badge(f.kind), badge(f.subject), ...f.entities.map((e) => badge(e)),
            h("span", {}, `${when(f.created)} · recalled ${f.use_count}×${f.last_used ? ` (${ago(f.last_used)})` : ""} · ${f.source}${f.status !== "active" ? ` · ${f.status}` : ""}`)));
      };
      const edit = () => {
        const text = h("textarea", { rows: 3, maxlength: "600" }); text.value = f.text;
        const kind = h("select", {}, KINDS.map((k) => h("option", { value: k }, k))); kind.value = f.kind;
        const importance = h("input", { type: "number", min: "0", max: "1", step: "0.1", value: f.importance });
        const names = h("input", { type: "text", value: f.entities.join(", "), placeholder: "names, comma separated" });
        clear(body);
        body.append(h("div", { class: "stack" }, text, h("div", { class: "row wrap" }, field("Kind", kind), field("Importance (0–1)", importance), field("Names", names)),
          h("div", { class: "row" },
            button("Save", async () => { if (await attempt(() => put(`/api/ltm/facts/${f.id}`, { text: text.value, kind: kind.value, importance: Number(importance.value), entities: names.value.split(",").map((s) => s.trim()).filter(Boolean) }), "Saved")) await reload(); }, { primary: true, small: true }),
            button("Cancel", show, { small: true }))));
      };
      show();
      const actions = [button("Edit", edit, { small: true, iconName: "edit" })];
      if (f.status === "active") actions.push(button("Archive", async () => { if (await attempt(() => post(`/api/ltm/facts/${f.id}/archive`), "Archived")) await reload(); }, { small: true }));
      else actions.push(button("Restore", async () => { if (await attempt(() => post(`/api/ltm/facts/${f.id}/restore`), "Restored")) await reload(); }, { small: true }));
      if (f.promotable) actions.push(button("Add to notes", async () => { if (await attempt(() => post(`/api/ltm/facts/${f.id}/promote`), "Added to MEMORY.md")) await reload(); }, { small: true, title: "Used often and important: copy into the notes sent with every request" }));
      actions.push(dangerButton("Delete", async () => { if (await attempt(() => del(`/api/ltm/facts/${f.id}`), "Deleted")) await reload(); }));
      return h("div", { class: "entry-row" }, body, h("div", { class: "row wrap" }, ...actions));
    }

    function factsPanel() {
      const subject = h("select", { "aria-label": "Subject" }, h("option", { value: "" }, "All subjects"), ...status.subjects.map((s) => h("option", { value: s.subject }, s.subject)));
      subject.value = filter.subject; subject.onchange = () => { filter.subject = subject.value; loadFacts(); };
      const state = h("select", { "aria-label": "State" }, ["active", "archived", "superseded", "all"].map((s) => h("option", { value: s }, s)));
      state.value = filter.state; state.onchange = () => { filter.state = state.value; loadFacts(); };
      const kind = h("select", { "aria-label": "Kind" }, h("option", { value: "" }, "All kinds"), KINDS.map((k) => h("option", { value: k }, k)));
      kind.value = filter.kind; kind.onchange = () => { filter.kind = kind.value; loadFacts(); };
      const search = h("input", { type: "search", placeholder: "Search the facts", "aria-label": "Search", value: filter.q });
      let timer = 0;
      search.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(async () => { filter.q = search.value; await loadFacts(); const el = view.querySelector("input[type=search]"); if (el) { el.focus(); el.setSelectionRange(filter.q.length, filter.q.length); } }, 250); });
      const list = h("div", {}, facts.map(factRow));
      if (!facts.length) list.append(empty(filter.q || filter.kind || filter.subject || filter.state !== "active" ? "Nothing matches." : status.mode === "off"
        ? "Long-term memory is off. Turn it on in Settings and the assistant starts keeping facts from your conversations." : "No facts yet. They appear after a conversation has been read, or add one yourself."));
      const adder = h("div", {});
      const add = button("New fact", () => {
        const sub = h("input", { type: "text", value: filter.subject || "owner", list: "ltm-subjects", maxlength: "140" });
        const text = h("textarea", { rows: 2, maxlength: "600", placeholder: "One short sentence that makes sense alone" });
        const kind2 = h("select", {}, KINDS.map((k) => h("option", { value: k }, k))); kind2.value = "fact";
        clear(adder);
        adder.append(h("div", { class: "stack pad-t" }, field("Subject", sub, "owner, user:<id>, chat:<id> or agent:<name>"), field("Fact", text), field("Kind", kind2),
          h("div", { class: "row" },
            button("Add", async () => { if (await attempt(() => post("/api/ltm/facts", { subject: sub.value.trim(), text: text.value, kind: kind2.value }), "Added")) { clear(adder); await reload(); } }, { primary: true, small: true }),
            button("Cancel", () => clear(adder), { small: true }))));
      }, { primary: true, small: true, iconName: "plus" });
      return panel("Facts", "memory", h("div", { class: "row wrap" }, subject, state, kind, search, add), adder, list);
    }

    // Subjects, export and import -------------------------------------------------------------------------
    function subjectsPanel() {
      if (!status.subjects.length) return null;
      const rows = status.subjects.map((s) => h("div", { class: "entry-row" },
        h("div", { class: "grow" }, h("span", { class: "mono" }, s.subject), " ", s.paused ? badge("paused", "warn") : null,
          h("span", { class: "muted small" }, ` ${["active", "archived", "superseded"].filter((k) => s[k]).map((k) => `${s[k]} ${k}`).join(", ")}`)),
        h("div", { class: "row wrap" },
          button(s.paused ? "Resume" : "Pause", async () => { if (await attempt(() => put(`/api/ltm/subjects/${encodeURIComponent(s.subject)}/pause`, { paused: !s.paused }), s.paused ? "Resumed" : "Paused")) await reload(); }, { small: true, title: "Stop storing and recalling for this subject; its facts are kept" }),
          h("a", { class: "btn btn-sm", href: `/api/ltm/export?subject=${encodeURIComponent(s.subject)}`, download: "" }, "Export"),
          dangerButton("Forget", async () => { if (await attempt(() => del(`/api/ltm/subjects/${encodeURIComponent(s.subject)}`), "Forgotten")) await reload(); }))));
      return panel("Subjects", "memory", h("p", { class: "muted small" }, "Whose facts they are. A subject never sees another's facts."), ...rows);
    }

    function transferPanel() {
      const file = h("input", { type: "file", accept: "application/json,.json", "aria-label": "Memory export to import" });
      const mode = h("select", { "aria-label": "Import mode" }, h("option", { value: "merge" }, "Merge: add what is missing"), h("option", { value: "replace" }, "Replace: first clear the subjects in the file"));
      const result = h("div", {});
      let data = null;
      const run = async (dry) => post(`/api/ltm/import?mode=${mode.value}&dry_run=${dry}`, data);
      const preview = button("Preview", null, { small: true });
      preview.onclick = busy(preview, async () => {
        clear(result);
        if (!file.files[0]) { result.append(h("p", { class: "muted" }, "Choose a file first.")); return; }
        try { data = JSON.parse(await file.files[0].text()); } catch { result.append(h("div", { class: "notice err" }, "That file is not valid JSON.")); return; }
        const r = await attempt(() => run(true));
        if (!r) return;
        const apply = button(`Import ${r.new} fact${r.new === 1 ? "" : "s"}`, null, { primary: true, small: true, disabled: r.new === 0 && !r.removed });
        apply.onclick = busy(apply, async () => { if (await attempt(() => run(false), "Imported")) { clear(result); file.value = ""; data = null; await reload(); } });
        result.append(h("div", { class: "stack pad-t" },
          h("div", {}, `${r.new} new, ${r.duplicates} already there${r.removed ? `, ${r.removed} would be removed first` : ""}, ${r.rejected.length} refused.`),
          Object.keys(r.subjects).length ? h("div", { class: "muted small" }, Object.entries(r.subjects).map(([s, n]) => `${s}: ${n}`).join(" · ")) : null,
          r.rejected.length ? h("ul", { class: "small" }, r.rejected.slice(0, 8).map((x) => h("li", {}, `row ${x.row}: ${x.reason}`))) : null,
          apply));
      });
      return panel("Export and import", "memory",
        h("div", { class: "row wrap" }, h("a", { class: "btn btn-sm", href: "/api/ltm/export", download: "" }, "Export all (JSON)"),
          h("a", { class: "btn btn-sm", href: "/api/ltm/export?format=md", download: "" }, "Export all (Markdown)")),
        h("p", { class: "muted small" }, "Vectors are not exported; they are made again on import. Imported facts go through the same checks as extracted ones."),
        h("div", { class: "stack pad-t" }, field("Import a JSON export", file), field("Mode", mode), h("div", { class: "row" }, preview)), result);
    }

    function draw() {
      clear(view);
      const list = h("datalist", { id: "ltm-subjects" }, ["owner", ...status.subjects.map((s) => s.subject)].filter((v, i, a) => a.indexOf(v) === i).map((s) => h("option", { value: s })));
      view.append(...[statusPanel(), openSettings ? settingsPanel() : null, factsPanel(), subjectsPanel(), transferPanel(), list].filter(Boolean));
      ctx.setCount(status.total);
    }
    await loadFacts();
  },
};
