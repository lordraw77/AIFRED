// Sessions: overview of the platforms and recent conversations, and full-text history search (spec 10.5).
import { ago, attempt, badge, button, clear, dangerButton, del, empty, field, get, h, icon, number, panel, put, skeleton, store } from "../core.js";
import { marked } from "../md.js";

export function exportMarkdown(id, messages) {
  const lines = [`# ${id}`, ""];
  for (const m of messages) {
    if (m.role === "user") lines.push("## You", "", m.content || "", "");
    else if (m.role === "assistant" && m.content) lines.push("## AIFRED", "", m.content, "");
    else if (m.role === "assistant") for (const c of m.tool_calls || []) lines.push(`> tool: ${c.function.name}(${c.function.arguments})`, "");
  }
  return lines.join("\n");
}

function download(name, text) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/markdown" }));
  const a = h("a", { href: url, download: name });
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export default {
  async render(root, ctx) {
    let view = store("sessions.view") === "history" ? "history" : "overview";
    let source = "";
    let query = "";
    const body = h("div", {});
    root.append(skeleton(4));

    async function draw() {
      const [sessions, status] = await Promise.all([get(`/api/sessions?limit=200${source ? `&source=${encodeURIComponent(source)}` : ""}`), get("/api/status")]);
      const all = source ? await get("/api/sessions?limit=200") : sessions;
      ctx.setCount(sessions.length);
      clear(root); clear(body);
      const sources = [...new Set(all.map((s) => s.source))].sort();
      const tg = status.telegram;
      root.append(
        h("div", { class: "stats" },
          h("div", { class: "stat" }, h("div", { class: "value" }, number(status.sessions)), h("div", { class: "label" }, "Total")),
          h("div", { class: "stat" }, h("div", { class: "value" }, number(status.messages)), h("div", { class: "label" }, "Msgs")),
          h("div", { class: "stat" }, h("div", { class: "value" }, String(status.sources.length)), h("div", { class: "label" }, "Sources")),
          h("div", { class: "stat" }, h("div", { class: "value ok" }, String((tg.state === "running" ? 1 : 0) + 1)), h("div", { class: "label" }, "Platforms"))),
        h("div", { class: "row between", role: "group" },
          h("div", { class: "row" },
            h("select", { "aria-label": "Source", onchange: (e) => { source = e.target.value; draw(); } }, h("option", { value: "" }, "All sources"), sources.map((s) => h("option", { value: s, selected: s === source }, s))),
            h("div", { class: "segmented", role: "tablist" }, ["overview", "history"].map((v) => h("button", { type: "button", role: "tab", "aria-selected": String(v === view), onclick: () => { view = v; store("sessions.view", v); draw(); } }, v)))),
        ),
        body);
      if (view === "overview") overview(sessions, all, status); else history(source);
    }

    function platformRow(name, up, last, note) {
      return h("div", { class: "row" }, h("span", { class: `dot ${up ? "ok" : "off"}` }, name), h("span", { class: "muted mono" }, `last update: ${last ? ago(last) : "never"}`), note && h("span", { class: "hint" }, note));
    }

    function overview(sessions, all, status) {
      const lastBy = {};
      for (const s of all) lastBy[s.source] = Math.max(lastBy[s.source] || 0, s.updated);
      const tg = status.telegram;
      const rows = [platformRow("api_server", true, lastBy.api || 0, "HTTP API and dashboard"), ...Object.keys(lastBy).filter((k) => !["api"].includes(k)).map((k) => platformRow(k, k === "telegram" ? tg.state === "running" : true, lastBy[k]))];
      body.append(panel("Connected platforms", "system", h("div", { class: "stack" }, rows)),
        panel("Recent sessions", "sessions", sessions.length ? sessions.slice(0, 30).map(sessionRow) : empty("No conversation yet.", button("Start one", () => { location.hash = "#/chat"; }, { primary: true }))));
    }

    function sessionRow(s) {
      const menu = h("span", { class: "row", hidden: true });
      const title = h("div", { class: "title" }, s.title || s.preview || "(untitled)");
      const toggle = button("", () => { menu.hidden = !menu.hidden; }, { small: true, title: "More", iconName: "edit" });
      const rename = h("input", { type: "text", value: s.title || "", maxlength: "200", "aria-label": "Title" });
      menu.append(
        rename, button("Save title", async () => { if (rename.value.trim() && await attempt(() => put(`/api/sessions/${encodeURIComponent(s.id)}`, { title: rename.value.trim() }), "Renamed")) draw(); }, { small: true }),
        button("Export", async () => { const m = await attempt(() => get(`/api/sessions/${encodeURIComponent(s.id)}/messages?limit=1000`)); if (m) download(`${s.id.replace(/[^A-Za-z0-9._-]/g, "_")}.md`, exportMarkdown(s.id, m)); }, { small: true }),
        dangerButton("Delete", async () => { if (await attempt(() => del(`/api/sessions/${encodeURIComponent(s.id)}`), "Deleted")) draw(); }));
      return h("div", { class: "srow row-hover" },
        h("div", { class: "main-col", role: "link", tabindex: "0", onclick: () => { location.hash = `#/chat?s=${encodeURIComponent(s.id)}`; }, onkeydown: (e) => { if (e.key === "Enter") location.hash = `#/chat?s=${encodeURIComponent(s.id)}`; } },
          title, h("div", { class: "mono muted" }, `${s.source} · ${s.message_count} msgs · ${ago(s.updated)}`), h("div", { class: "preview" }, s.preview)),
        h("div", { class: "stack" }, toggle, menu));
    }

    function history(src) {
      const input = h("input", { type: "text", placeholder: "Search every conversation (press / to focus)", value: query, "aria-label": "Search" });
      const results = h("div", {});
      const run = async () => {
        query = input.value.trim(); clear(results);
        if (!query) return;
        const rows = await attempt(() => get(`/api/search?q=${encodeURIComponent(query)}&limit=50${src ? `&source=${encodeURIComponent(src)}` : ""}`)) || [];
        if (!rows.length) results.append(empty("Nothing found."));
        for (const r of rows) {
          results.append(h("div", { class: "srow row-hover" }, h("div", { class: "main-col", role: "link", tabindex: "0", onclick: () => { location.hash = `#/chat?s=${encodeURIComponent(r.session_id)}`; } },
            h("div", { class: "title" }, r.title || r.session_id), h("div", { class: "mono muted" }, `${r.source} · ${r.role} · ${ago(r.ts)}`), h("div", {}, marked(r.snippet)))));
        }
      };
      input.addEventListener("keydown", (e) => { if (e.key === "Enter") run(); });
      body.append(panel("History", "logs", h("div", { class: "row" }, input, button("Search", run, { primary: true, small: true }))), results);
      window.__focusSearch = () => input.focus();
      if (query) run();
    }

    await draw();
    return () => { window.__focusSearch = null; };
  },
};
