// Memory: MEMORY.md and USER.md as lists of entries with a usage bar; add, edit and remove entries.
import { attempt, button, clear, dangerButton, del, empty, field, get, h, panel, post, put, skeleton } from "../core.js";

const TITLES = { memory: "What I know (MEMORY.md)", user: "About you (USER.md)" };

export default {
  async render(root, ctx) {
    root.append(skeleton(4));
    const data = {};
    const load = async () => { for (const t of ["memory", "user"]) data[t] = await get(`/api/memory/${t}`); };
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
        h("div", { class: "row between" }, h("span", { class: "mono" }, m.counter + " characters"), h("span", { class: "muted" }, `${pct}%`)), bar, list,
        h("div", { class: "stack pad-t" }, input, h("div", { class: "row" }, button("Add", async () => {
          if (input.value.trim() && await attempt(() => post(`/api/memory/${target}`, { content: input.value.trim() }), "Added")) { await load(); draw(); }
        }, { primary: true, small: true, iconName: "plus" }))));
    };
    function draw() { clear(view); view.append(section("memory"), section("user")); ctx.setCount(data.memory.entries.length + data.user.entries.length); }
    draw();
  },
};
