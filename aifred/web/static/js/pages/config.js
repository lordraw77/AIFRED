// Config: every setting grouped by section, with a filter. Changes apply live; secrets are write-only (spec 4.3).
import { attempt, button, clear, empty, get, h, put, skeleton, toast } from "../core.js";

const GROUP_ORDER = ["llm", "routing", "agent", "tools", "telegram", "scheduler", "agents", "mcp", "rag", "review", "system"];

function asText(item, value) {
  if (value === null || value === undefined) return "";
  if (item.kind === "list" || item.kind === "int_list") return value.join(", ");
  if (item.kind === "json") return JSON.stringify(value);
  return String(value);
}

export default {
  async render(root, ctx) {
    root.append(skeleton(5));
    let items;
    try { items = await get("/api/config"); } catch (err) { clear(root); throw err; }
    clear(root);
    ctx.setCount(items.length);

    const original = new Map();   // key -> text shown when loaded
    const inputs = new Map();     // key -> {read(): text, node, row, clearFlag}
    const filter = h("input", { type: "text", placeholder: "Filter settings (press / to focus)", "aria-label": "Filter settings" });
    const bar = h("div", { class: "savebar", hidden: true });
    const groupsEl = h("div", {});

    function control(item) {
      const text = asText(item, item.value);
      original.set(item.key, text);
      let node, read;
      if (item.secret) {
        node = h("input", { type: "password", autocomplete: "new-password", placeholder: item.set ? "•••• (set; type to replace)" : "not set" });
        const entry = { node, cleared: false, row: null, read: () => (entry.cleared ? "" : node.value), isDirty: () => entry.cleared || node.value !== "" };
        const wrap = h("div", { class: "row" }, node, item.set && button("Clear", () => { entry.cleared = true; node.value = ""; node.placeholder = "will be cleared"; changed(); }, { small: true }));
        node.addEventListener("input", changed);
        inputs.set(item.key, entry);
        return wrap;
      }
      if (item.kind === "bool") {
        node = h("input", { type: "checkbox" }); node.checked = item.value === true;
        read = () => String(node.checked);
        original.set(item.key, String(item.value === true));
      } else if (item.kind === "enum") {
        node = h("select", {}, (item.choices || []).map((c) => h("option", { value: c }, c))); node.value = text; read = () => node.value;
      } else if (item.kind === "json") {
        node = h("textarea", { rows: 2 }); node.value = text; read = () => node.value;
      } else {
        node = h("input", { type: item.kind === "int" || item.kind === "float" ? "number" : "text", step: item.kind === "float" ? "any" : null }); node.value = text; read = () => node.value;
      }
      node.addEventListener("input", changed); node.addEventListener("change", changed);
      inputs.set(item.key, { node, read, isDirty: () => read() !== original.get(item.key) });
      return item.kind === "bool" ? h("label", { class: "check" }, node, h("span", {}, "on")) : node;
    }

    function changed() {
      const dirty = [...inputs].filter(([, c]) => c.isDirty()).map(([k]) => k);
      for (const [key, c] of inputs) c.row && c.row.classList.toggle("dirty", dirty.includes(key));
      bar.hidden = dirty.length === 0;
      clear(bar);
      if (dirty.length) {
        bar.append(h("span", { class: "mono" }, `${dirty.length} unsaved change${dirty.length === 1 ? "" : "s"}`),
          button("Save", save, { primary: true, small: true }), button("Discard", () => draw(), { small: true }));
      }
    }

    async function save() {
      const values = {};
      for (const [key, c] of inputs) if (c.isDirty()) values[key] = c.read();
      const fresh = await attempt(() => put("/api/config", { values }), "Saved. Changes apply now.");
      if (fresh) { items = fresh; draw(); }
    }

    function draw() {
      inputs.clear(); original.clear(); clear(groupsEl);
      const q = filter.value.trim().toLowerCase();
      const byGroup = new Map();
      for (const item of items) {
        if (q && !`${item.key} ${item.description} ${item.group}`.toLowerCase().includes(q)) continue;
        if (!byGroup.has(item.group)) byGroup.set(item.group, []);
        byGroup.get(item.group).push(item);
      }
      if (!byGroup.size) groupsEl.append(empty("No setting matches the filter."));
      const names = [...byGroup.keys()].sort((a, b) => (GROUP_ORDER.indexOf(a) + 1 || 99) - (GROUP_ORDER.indexOf(b) + 1 || 99));
      for (const group of names) {
        const rows = byGroup.get(group).map((item) => {
          const ctl = control(item);
          const row = h("div", { class: "cfg-row" },
            h("div", {}, h("div", { class: "key" }, item.key), h("div", { class: "hint" }, item.description)),
            h("div", { class: "stack" }, ctl, !item.secret && item.default !== null && item.default !== undefined && asText(item, item.default) !== asText(item, item.value)
              ? h("span", { class: "hint" }, `default: ${asText(item, item.default) || "(empty)"}`) : null));
          inputs.get(item.key).row = row;
          return row;
        });
        groupsEl.append(h("section", { class: "panel" }, h("header", { class: "panel-head" }, h("h2", {}, group), h("span", { class: "count" }, String(rows.length))), h("div", { class: "panel-body" }, rows)));
      }
      changed();
    }

    filter.addEventListener("input", () => {
      const unsaved = [...inputs].filter(([, c]) => c.isDirty());
      if (unsaved.length) { toast("Save or discard your changes before filtering", "err"); filter.value = ""; return; }
      draw();
    });
    const onKey = (e) => { if (e.key === "/" && !["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName)) { e.preventDefault(); filter.focus(); } };
    document.addEventListener("keydown", onKey);

    root.append(h("div", { class: "field pad-b" }, filter), groupsEl, bar);
    draw();
    return () => document.removeEventListener("keydown", onKey);
  },
};
