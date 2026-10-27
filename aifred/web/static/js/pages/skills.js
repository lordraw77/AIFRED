// Skills: list, Markdown preview, body editing and deletion (the agent can create skills but not delete them).
import { assistButtons } from "../assist.js";
import { attempt, badge, button, clear, dangerButton, del, empty, field, get, h, panel, put, skeleton } from "../core.js";
import { renderMarkdown } from "../md.js";

export default {
  async render(root, ctx) {
    root.append(skeleton(4));
    let skills = await get("/api/skills");
    let current = null;      // {name, description, body, isNew}
    clear(root);
    const left = h("div", { class: "chat-list" });
    const right = h("div", {});
    root.append(h("div", { class: "split" }, h("div", { class: "stack" }, button("New skill", () => edit({ name: "", description: "", body: "", isNew: true }), { primary: true, small: true, iconName: "plus" }), left), right));

    function drawList() {
      clear(left);
      ctx.setCount(skills.length);
      if (!skills.length) left.append(h("p", { class: "muted pad" }, "No skills yet."));
      for (const s of skills) left.append(h("button", { class: "list-item", type: "button", "aria-current": current && current.name === s.name ? "true" : null, onclick: async () => {
        const full = await attempt(() => get(`/api/skills/${encodeURIComponent(s.name)}`));
        if (full) edit(full);
      } }, h("span", { class: "s-title mono" }, s.name), h("span", { class: "s-meta" }, s.description)));
    }

    function edit(skill) {
      current = skill; drawList(); clear(right);
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
        if (await attempt(() => del(`/api/skills/${encodeURIComponent(skill.name)}`), "Skill deleted")) { skills = await get("/api/skills"); current = null; clear(right); drawList(); }
      }));
      right.append(panel(skill.isNew ? "New skill" : skill.name, "skills", field("Name", name, skill.isNew ? "Letters, digits, - and _" : null), field("Description", description, "One line: when should the agent use it?"), assistButtons({ kind: "skill.description", getText: () => description.value, setText: (t) => { description.value = t; }, getContext: () => `Skill name: ${name.value}\n${body.value.slice(0, 1200)}` }), mode, body, preview, actions));
    }

    drawList();
    if (!skills.length) right.append(empty("Skills are saved procedures the agent can load when a task matches. Create the first one."));
  },
};
