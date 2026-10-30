// Skills: searchable card grid, Markdown preview, body editing and deletion (the agent can create skills but not delete them).
import { assistButtons, draftNote, generatorBox } from "../assist.js";
import { attempt, badge, button, clear, dangerButton, del, empty, field, get, h, panel, put, skeleton } from "../core.js";
import { renderMarkdown } from "../md.js";

export default {
  async render(root, ctx) {
    root.append(skeleton(4));
    let skills = await get("/api/skills");
    let current = null;      // {name, description, body, isNew}
    let query = "";
    let showGen = false;
    clear(root);
    const view = h("div", {});
    root.append(view);

    function drawList() {
      current = null; clear(view);
      ctx.setCount(skills.length);
      const search = h("input", { type: "search", placeholder: "Search skills", "aria-label": "Search skills", value: query });
      const grid = h("div", { class: "skill-grid" });
      const fill = () => {
        clear(grid);
        const q = query.trim().toLowerCase();
        const shown = skills.filter((x) => !q || x.name.toLowerCase().includes(q) || x.description.toLowerCase().includes(q)).sort((a, b) => a.name.localeCompare(b.name));
        for (const x of shown) grid.append(h("button", { class: "skill-card", type: "button", onclick: async () => {
          const full = await attempt(() => get(`/api/skills/${encodeURIComponent(x.name)}`));
          if (full) edit(full);
        } }, h("span", { class: "s-title mono" }, x.name), h("span", { class: "skill-desc" }, x.description)));
        if (!shown.length) grid.append(h("p", { class: "muted pad" }, skills.length ? "No skill matches." : "No skills yet."));
      };
      search.addEventListener("input", () => { query = search.value; fill(); });
      fill();
      view.append(h("div", { class: "row pad-b" }, search, button("Generate with AI", () => { showGen = !showGen; drawList(); }, { small: true, iconName: "edit" }),
        button("New skill", () => edit({ name: "", description: "", body: "", isNew: true }), { primary: true, small: true, iconName: "plus" })));
      if (showGen) view.append(h("div", { class: "pad-b" }, generatorBox({ kind: "skill", label: "What should the new skill do?", placeholder: "For example: check why an Nginx site returns 502 or 504 and propose the smallest fix.",
        onDraft: (r) => { showGen = false; edit({ ...r.draft, isNew: true }, draftNote(r)); } })));
      view.append(grid);
      if (!skills.length) view.append(empty("Skills are saved procedures the agent can load when a task matches. Create the first one."));
    }

    function edit(skill, note = null) {
      current = skill; clear(view);
      const name = h("input", { type: "text", value: skill.name, readonly: !skill.isNew || null, pattern: "[a-z0-9][a-z0-9_\\-]{0,63}", placeholder: "lowercase-name" });
      const description = h("input", { type: "text", value: skill.description, maxlength: "500" });
      const body = h("textarea", { rows: 16 }); body.value = skill.body;
      const preview = h("div", { hidden: true });
      const mode = h("div", { class: "segmented", role: "tablist" });
      const show = (which) => {
        body.hidden = which === "preview"; preview.hidden = which !== "preview";
        if (which === "preview") { clear(preview); preview.append(renderMarkdown(body.value)); }
        for (const b of mode.children) b.setAttribute("aria-selected", String(b.dataset.m === which));
      };
      for (const m of ["edit", "preview"]) mode.append(h("button", { type: "button", role: "tab", "data-m": m, "aria-selected": String(m === "edit"), onclick: () => show(m) }, m));
      const actions = h("div", { class: "row" }, button("Save", async () => {
        const saved = await attempt(() => put(`/api/skills/${encodeURIComponent(name.value.trim())}`, { description: description.value, body: body.value }), "Skill saved");
        if (saved) { skills = await get("/api/skills"); edit(saved); }
      }, { primary: true, small: true }));
      if (!skill.isNew) actions.append(dangerButton("Delete", async () => {
        if (await attempt(() => del(`/api/skills/${encodeURIComponent(skill.name)}`), "Skill deleted")) { skills = await get("/api/skills"); drawList(); }
      }));
      view.append(h("div", { class: "pad-b" }, button("← All skills", drawList, { small: true })), note, panel(skill.isNew ? "New skill" : skill.name, "skills", field("Name", name, skill.isNew ? "Letters, digits, - and _" : null), field("Description", description, "One line: when should the agent use it?"), assistButtons({ kind: "skill.description", getText: () => description.value, setText: (t) => { description.value = t; }, getContext: () => `Skill name: ${name.value}\n${body.value.slice(0, 1200)}` }), mode, body, preview, actions));
    }

    drawList();
  },
};
