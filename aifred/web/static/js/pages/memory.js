// Memory: MEMORY.md and USER.md as lists of entries with a usage bar; add, edit and remove entries.
import { attempt, button, clear, dangerButton, del, empty, field, get, h, panel, post, put, skeleton } from "../core.js";
import { drawGraph, legend } from "../graph.js";

const TITLES = { memory: "What I know (MEMORY.md)", user: "About you (USER.md)" };

export default {
  async render(root, ctx) {
    root.append(skeleton(4));
    const data = {};
    const load = async () => { for (const t of ["memory", "user"]) data[t] = await get(`/api/memory/${t}`); data.graph = await get("/api/memory/graph/view"); };
    await load();
    const view = h("div", {});
    clear(root); root.append(view);

    const section = (target) => {
      const m = data[target];
      const pct = Math.min(100, Math.round((m.used / m.limit) * 100));
      const bar = h("div", { class: "bar", role: "img", "aria-label": `${pct}% used` }, h("span", {}));
      bar.firstChild.style.setProperty("--pct", String(pct));
      const list = h("div", {});
      for (const entry of m.entries) {
        const text = h("div", { class: "text" }, entry);
        const row = h("div", { class: "entry-row" }, text);
        const edit = button("Edit", () => {
          const area = h("textarea", { rows: 3, maxlength: "2000" }); area.value = entry;
          clear(row);
          row.append(h("div", { class: "stack" }, area, h("div", { class: "row" },
            button("Save", async () => { if (await attempt(() => put(`/api/memory/${target}`, { match: entry, content: area.value }), "Saved")) { await load(); draw(); } }, { primary: true, small: true }),
            button("Cancel", draw, { small: true }))));
          row.firstChild.classList.add("grow");
        }, { small: true, iconName: "edit" });
        row.append(edit, dangerButton("Remove", async () => { if (await attempt(() => del(`/api/memory/${target}?entry=${encodeURIComponent(entry)}`), "Removed")) { await load(); draw(); } }));
        list.append(row);
      }
      if (!m.entries.length) list.append(empty("Nothing here yet. The agent adds facts as it learns them, or add one yourself."));
      const input = h("textarea", { rows: 2, maxlength: "2000", placeholder: "Add an entry" });
      return panel(TITLES[target], "memory",
        h("div", { class: "row between" }, h("span", { class: "mono" }, m.counter + " characters"), h("span", { class: "muted" }, `${pct}%`)), bar,
        h("p", { class: "muted small" }, "Size: ", h("a", { href: "#/config" }, target === "memory" ? "MEMORY_LIMIT" : "USER_LIMIT"), " in Config."), list,
        h("div", { class: "stack pad-t" }, input, h("div", { class: "row" }, button("Add", async () => {
          if (input.value.trim() && await attempt(() => post(`/api/memory/${target}`, { content: input.value.trim() }), "Added")) { await load(); draw(); }
        }, { primary: true, small: true, iconName: "plus" }))));
    };
    const graphPanel = (graph) => {
      if (!graph.nodes.length) return panel("Graph", "memory", empty("No names yet. Entries that put people, hosts or projects between double brackets, like [[Marco]] works at [[Acme]], appear here as a graph."));
      const detail = h("div", { class: "muted" }, "Click a name to see its entries.");
      const pick = (node) => {
        clear(detail);
        const rows = h("ul", { class: "g-entries" }, node.entries.map((e) => h("li", {}, h("span", { class: "muted" }, e.target === "user" ? "profile: " : "notes: "), e.text)));
        detail.append(h("strong", {}, `${node.label} · ${node.count}`), rows);
      };
      const loose = graph.unlinked.length ? h("p", { class: "muted" }, `${graph.unlinked.length} ${graph.unlinked.length === 1 ? "entry mentions" : "entries mention"} no name and ${graph.unlinked.length === 1 ? "is" : "are"} not in the graph.`) : null;
      return panel("Graph", "memory", legend(), drawGraph(graph, pick), detail, loose);
    };
    function draw() { clear(view); view.append(section("memory"), section("user"), graphPanel(data.graph)); ctx.setCount(data.memory.entries.length + data.user.entries.length); }
    draw();
  },
};
