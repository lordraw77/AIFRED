// Agents: named sub-agents the main agent can hand tasks to, each with its own role, model route, toolsets and limits.
import { attempt, badge, busy, button, clear, dangerButton, del, empty, field, get, h, panel, post, put, skeleton, ago } from "../core.js";

const STATUS = { ok: "ok", error: "err", running: "warn" };

export default {
  async render(root, ctx) {
    let editing = null;      // agent name being edited, "" for a new one
    let opened = null;       // agent whose history is open
    let testing = null;      // agent with the test box open
    const body = h("div", {});
    root.append(body);

    async function draw() {
      const data = await get("/api/agents");
      ctx.setCount(data.agents.length);
      clear(body);
      const head = panel("Sub-agents", "models");
      head.querySelector(".panel-head").append(button("New agent", () => { editing = ""; draw(); }, { primary: true, small: true, iconName: "plus" }));
      const inner = head.querySelector(".panel-body");
      if (!data.agents.length && editing === null) inner.append(empty("No sub-agent yet. A sub-agent is a specialist the main assistant can hand a task to, for example a researcher for the web."));
      if (editing === "") inner.append(form(null, data));
      for (const a of data.agents) inner.append(editing === a.name ? form(a, data) : card(a, data));
      body.append(head, h("p", { class: "hint" }, "Add agents to the toolsets of a gateway (agents on the Config page) so the main assistant can use delegate. An agent never gets more than the gateway it is called from, cannot delegate further and is never available in an open Telegram chat. You can also call one yourself with /agent <name> <task>."));
    }

    function card(a, data) {
      const testBox = h("div", { class: "stack", hidden: testing !== a.name });
      if (testing === a.name) buildTest(a, testBox);
      const log = h("div", { class: "stack", hidden: opened !== a.name });
      if (opened === a.name) loadRuns(a.name, log);
      return h("div", { class: "item" },
        h("div", { class: "item-head" }, h("span", { class: `dot ${a.enabled ? "ok" : "off"}` }), h("span", { class: "title" }, a.name), badge(a.route), !a.enabled && badge("off", "warn"),
          badge(`memory: ${a.memory}`),
          button(a.enabled ? "Turn off" : "Turn on", async () => { if (await attempt(() => put(`/api/agents/${encodeURIComponent(a.name)}`, { enabled: !a.enabled }))) draw(); }, { small: true }),
          button("Test", () => { testing = testing === a.name ? null : a.name; draw(); }, { small: true }),
          button("History", () => { opened = opened === a.name ? null : a.name; draw(); }, { small: true }),
          button("Edit", () => { editing = a.name; draw(); }, { small: true, iconName: "edit" }),
          dangerButton("Delete", async () => { if (await attempt(() => del(`/api/agents/${encodeURIComponent(a.name)}`), "Agent deleted")) draw(); })),
        h("div", {}, a.description),
        h("div", { class: "hint" }, `toolsets: ${a.toolsets.join(", ") || "none"}${a.skills.length ? ` · skills: ${a.skills.join(", ")}` : ""} · up to ${a.max_iterations || data.defaults.max_iterations} rounds, ${a.timeout || data.defaults.timeout} s${a.token_budget ? `, ${a.token_budget} tokens` : ""}`),
        testBox, log);
    }

    function buildTest(a, into) {
      const task = h("textarea", { rows: "3", placeholder: "A task to try this agent on" });
      const run = button("Run", null, { primary: true });
      const out = h("div", {});
      run.onclick = busy(run, async () => {
        clear(out); out.append(skeleton(2));
        const r = await attempt(() => post(`/api/agents/${encodeURIComponent(a.name)}/run`, { task: task.value }));
        clear(out);
        if (r) out.append(r.status === "ok" ? h("div", { class: "pre" }, r.output) : h("div", { class: "notice err" }, r.error), h("div", { class: "hint" }, `${r.tokens || 0} tokens${r.model ? ` · ${r.model}` : ""} · session ${r.session_id}`));
      });
      into.hidden = false;
      into.append(task, h("div", { class: "row" }, run), out);
    }

    async function loadRuns(name, into) {
      into.hidden = false;
      into.append(skeleton(2));
      const runs = await get(`/api/agents/${encodeURIComponent(name)}/runs`);
      clear(into);
      if (!runs.length) into.append(h("div", { class: "hint" }, "No run yet."));
      for (const r of runs) {
        into.append(h("div", { class: "item" },
          h("div", { class: "item-head" }, badge(r.status, STATUS[r.status] || ""), h("span", { class: "muted" }, ago(r.started)), r.tokens ? badge(`${r.tokens} tokens`) : null, h("span", { class: "mono muted" }, r.session_id)),
          h("div", { class: "hint" }, `task: ${r.task.slice(0, 300)}`),
          r.error ? h("div", { class: "notice err" }, r.error) : null,
          r.output ? h("div", { class: "pre" }, r.output) : null));
      }
    }

    function form(a, data) {
      const isNew = !a;
      const name = h("input", { type: "text", value: a ? a.name : "", readonly: !isNew || null, placeholder: "sysadmin", pattern: "[a-z0-9][a-z0-9_\\-]{0,29}", maxlength: "30" });
      const description = h("input", { type: "text", value: a ? a.description : "", maxlength: "240", placeholder: "Inspects my servers through the MCP tools. Use it for questions about hosts and services." });
      const role = h("textarea", { rows: "5", maxlength: "4000", placeholder: "You are a careful systems administrator. Look before you act…" }, a ? a.role : "");
      const route = h("select", {}, ...data.routes.map((r) => h("option", { value: r }, r)));
      route.value = a ? a.route : "default";
      const toolsets = h("input", { type: "text", value: a ? a.toolsets.join(", ") : "web", placeholder: "web, search, mcp:linux_ssh" });
      const skills = h("input", { type: "text", value: a ? a.skills.join(", ") : "", placeholder: "skill names, comma separated" });
      const memory = h("select", {}, ...data.memory_modes.map((m) => h("option", { value: m }, m)));
      memory.value = a ? a.memory : "none";
      const num = (value, placeholder) => h("input", { type: "number", min: "0", value: value ?? "", placeholder });
      const rounds = num(a && a.max_iterations, String(data.defaults.max_iterations));
      const timeout = num(a && a.timeout, String(data.defaults.timeout));
      const budget = num(a && a.token_budget, "unlimited");
      const list = (el) => el.value.split(",").map((x) => x.trim()).filter(Boolean);
      const count = (el) => (el.value === "" ? null : Number(el.value));
      const save = button("Save", null, { primary: true });
      save.onclick = busy(save, async () => {
        const values = { description: description.value, role: role.value, route: route.value, toolsets: list(toolsets), skills: list(skills), memory: memory.value,
          max_iterations: count(rounds), timeout: count(timeout), token_budget: count(budget) };
        const call = isNew ? () => post("/api/agents", { name: name.value.trim(), ...values }) : () => put(`/api/agents/${encodeURIComponent(a.name)}`, values);
        if (await attempt(call, "Saved")) { editing = null; draw(); }
      });
      return h("div", { class: "item stack" },
        field("Name", name), field("What it is for", description, "One line the main assistant reads to decide when to hand a task over."),
        field("Role", role, "The agent's instructions. It replaces the main persona and sees nothing of the conversation but the task."),
        field("Model route", route), field("Toolsets", toolsets, `Choose from: ${data.toolsets.join(", ")}, mcp:<server>. Limited to what the calling gateway has.`),
        field("Skills", skills, `Preloaded into its prompt. Available: ${data.skills.join(", ") || "none yet"}.`),
        field("Memory", memory, "none: no access; read: sees your notes and profile; write: may also save (WRITE_APPROVAL applies)."),
        field("Tool rounds", rounds), field("Time limit (s)", timeout), field("Token budget", budget, "Empty means no limit."),
        h("div", { class: "row" }, save, button("Cancel", () => { editing = null; draw(); })));
    }

    await draw();
  },
};
