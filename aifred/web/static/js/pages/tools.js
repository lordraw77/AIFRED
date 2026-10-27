// Tools: which toolsets are on for each gateway (click a switch to change it) and how often each tool was called.
import { attempt, badge, button, clear, empty, get, h, number, panel, post, put, del, skeleton, toast } from "../core.js";
import { instructionEditor } from "../instr.js";
import { toolsetSwitches } from "../toolsets.js";

export default {
  async render(root, ctx) {
    root.append(skeleton(4));
    let [data, notes] = await Promise.all([get("/api/tools"), get("/api/instructions")]);
    clear(root);
    ctx.setCount(data.toolsets.length);
    const open = new Set();
    const body = h("div", {});
    const reload = async () => { notes = await get("/api/instructions"); drawNotes(); };
    const notesEl = h("div", {});
    root.append(h("p", { class: "hint" }, "Click a switch to turn a toolset on or off for one gateway; the lists on the Config page are updated for you. Counts are calls since the server started."), body);

    function draw() {
      clear(body);
      const rows = [];
      for (const ts of data.toolsets) {
        rows.push(h("tr", { class: "row-hover" },
          h("td", {}, h("button", { class: "btn btn-sm", type: "button", "aria-expanded": String(open.has(ts.name)), onclick: () => { open.has(ts.name) ? open.delete(ts.name) : open.add(ts.name); draw(); } }, ts.name), " ", h("span", { class: "muted" }, `${ts.tools.length} tools`)),
          ...data.gateways.map((g) => h("td", {}, h("button", {
            class: "switch-btn", type: "button", role: "switch", "aria-checked": String(Boolean(ts.enabled[g])), "aria-label": `${ts.name} for ${g}`, title: `Turn ${ts.name} ${ts.enabled[g] ? "off" : "on"} for ${g}`,
            onclick: async () => { if (await attempt(() => put(`/api/toolsets/${encodeURIComponent(ts.name).replace(/%3A/gi, ":")}`, { gateways: [g], on: !ts.enabled[g] }))) { data = await get("/api/tools"); draw(); } },
          }, h("span", { class: `switch${ts.enabled[g] ? " on" : ""}` }), " ", h("span", { class: "muted mono" }, ts.enabled[g] ? "on" : "off")))),
          h("td", { class: "mono" }, number(ts.calls))));
        if (open.has(ts.name)) {
          rows.push(h("tr", {}, h("td", { colspan: String(data.gateways.length + 2) }, ts.tools.map((t) => h("div", { class: "row" },
            h("span", { class: "mono" }, t.name), t.restricted_ok ? badge("open bot ok") : null, !t.available ? badge("not available now", "warn") : null, h("span", { class: "muted" }, t.description), h("span", { class: "mono" }, `${t.calls} calls`))))));
        }
      }
      body.append(panel("Toolsets", "tools", data.toolsets.length ? h("table", {}, h("thead", {}, h("tr", {}, ["Toolset", ...data.gateways, "Calls"].map((x) => h("th", {}, x)))), h("tbody", {}, rows)) : empty("No tool registered.")));
    }
    // Instructions: what each MCP server, tool group and tool is for ------------------------------------

    function toolRow(t) {
      const input = h("input", { type: "text", value: t.text, maxlength: "2000", placeholder: "optional note for this tool", "aria-label": `Note for ${t.name}` });
      return h("div", { class: "tool-row" }, h("span", { class: "mono" }, t.name),
        input, h("span", { class: "row" },
          button("Save", async () => { if (!input.value.trim()) return; if (await attempt(() => put(`/api/instructions/${t.scope}`, { text: input.value }), "Saved")) reload(); }, { small: true }),
          t.stored ? button("Clear", async () => { if (await attempt(() => del(`/api/instructions/${t.scope}`), "Cleared")) reload(); }, { small: true }) : null));
    }

    const openGroups = new Set();
    function group(g) {
      const documented = g.stored && g.text;
      const body = h("div", { class: "stack", hidden: !openGroups.has(g.scope) },
        instructionEditor({ scope: g.scope, text: g.text, enabled: g.enabled, onChange: reload, rows: 3 }),
        h("div", { class: "label" }, "Notes for single tools"), g.tools.map(toolRow));
      return h("div", { class: "item" },
        h("div", { class: "item-head" }, h("span", { class: "title" }, g.scope), h("span", { class: "muted" }, `${g.tools.length} tools`),
          g.kind === "mcp" && !documented ? badge("undocumented", "warn") : null, documented && !g.enabled ? badge("not used", "warn") : null, documented ? badge("documented", "ok") : null,
          button(openGroups.has(g.scope) ? "Close" : "Edit", () => { openGroups.has(g.scope) ? openGroups.delete(g.scope) : openGroups.add(g.scope); drawNotes(); }, { small: true, iconName: "edit" })),
        documented ? h("div", { class: "hint" }, g.text.slice(0, 200)) : null, body);
    }

    function drawNotes() {
      clear(notesEl);
      const groups = [...notes.servers.map((x) => ({ ...x, scope: `mcp:${x.name}` })), ...notes.toolsets.map((x) => ({ ...x, scope: `toolset:${x.name}` }))];
      const pct = Math.min(100, Math.round((notes.used / notes.budget) * 100));
      const bar = h("div", { class: "bar", role: "img", "aria-label": `${pct}% of the budget` }, h("span", {})); bar.firstChild.style.setProperty("--pct", String(pct));
      notesEl.append(panel("Instructions: what is it for?", "memory",
        h("p", { class: "hint" }, "A tool schema says how to call a tool, not why it exists. Write when each MCP server or tool group should be used; the agent reads this in its prompt (Capabilities) and can ask for the full text with tool_help. Groups with nothing written are not listed, except MCP servers, which are flagged."),
        h("div", { class: "row between" }, h("span", { class: "mono" }, `Capabilities section: ${notes.used.toLocaleString()} / ${notes.budget.toLocaleString()} characters`),
          button("Apply to open conversations", async () => { if (await attempt(() => post("/api/instructions/reload"), "The next message uses the new text")) { /* done */ } }, { small: true, iconName: "refresh" })),
        bar,
        notes.preview ? h("details", {}, h("summary", { class: "muted" }, "What the agent sees"), h("div", { class: "pre" }, notes.preview)) : null,
        groups.map(group)));
    }

    root.append(notesEl);
    draw();
    drawNotes();
  },
};
