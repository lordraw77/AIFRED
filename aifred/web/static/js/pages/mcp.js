// MCP: servers with state, detected protocol, tool count and latency; add, edit, enable, delete, test and reload (spec 9.2).
import { attempt, badge, busy, button, clear, dangerButton, del, empty, field, get, h, panel, post, put, skeleton, toast } from "../core.js";
import { instructionEditor } from "../instr.js";
import { toolsetSwitches } from "../toolsets.js";

const DOT = { connected: "ok", error: "err", disabled: "off", connecting: "warn" };

export default {
  async render(root, ctx) {
    let editing = null;      // server name being edited, "" for a new one
    let notes = {};          // server name -> its instruction record
    const body = h("div", {});
    root.append(body);

    async function draw() {
      const [servers, overview] = await Promise.all([get("/api/mcp/servers"), get("/api/instructions")]);
      notes = Object.fromEntries(overview.servers.map((x) => [x.name, x]));
      ctx.setCount(servers.length);
      clear(body);
      const head = panel("Servers", "mcp");
      head.querySelector(".panel-head").append(button("Reload", async () => { await attempt(() => post("/api/mcp/reload"), "Reloading"); setTimeout(draw, 800); }, { small: true, iconName: "refresh" }), button("Add server", () => { editing = ""; draw(); }, { primary: true, small: true, iconName: "plus" }));
      const inner = head.querySelector(".panel-body");
      if (!servers.length && editing === null) inner.append(empty("No MCP server yet. A server gives the agent more tools, for example your Proxmox or Linux hosts."));
      if (editing === "") inner.append(form(null));
      for (const s of servers) inner.append(editing === s.name ? form(s) : card(s));
      body.append(head, h("p", { class: "hint" }, "Use the switches on each server to choose the gateways that may use its tools; the lists on the Config page are updated for you."));
    }

    function card(s) {
      const result = h("span", { class: "muted" });
      const test = button("Test", null, { small: true });
      test.onclick = busy(test, async () => {
        const r = await attempt(() => post(`/api/mcp/servers/${encodeURIComponent(s.name)}/test`));
        if (r) toast(r.ok ? `Connected, protocol ${r.protocol}, ${r.tools.length} tools in ${r.latency_ms} ms` : `Failed: ${r.error}`, r.ok ? "ok" : "err");
      });
      return h("div", { class: "item" },
        h("div", { class: "item-head" }, h("span", { class: `dot ${DOT[s.state] || "off"}` }), h("span", { class: "title" }, s.name), badge(s.state, DOT[s.state] === "ok" ? "ok" : DOT[s.state] === "err" ? "err" : ""),
          s.protocol && badge(s.protocol), s.tls_unverified && badge("TLS not verified", "warn"), !s.enabled && badge("disabled", "warn"),
          test, button("Edit", () => { editing = s.name; draw(); }, { small: true, iconName: "edit" }),
          dangerButton("Delete", async () => { if (await attempt(() => del(`/api/mcp/servers/${encodeURIComponent(s.name)}`), "Server deleted")) draw(); })),
        h("div", { class: "mono muted" }, s.url), h("div", { class: "mono" }, `${s.tools} tools exposed${s.latency_ms !== null && s.latency_ms !== undefined ? ` · ${s.latency_ms} ms` : ""} · call timeout ${s.timeout} s`),
        s.error && h("div", { class: "notice err" }, s.error),
        s.enabled ? toolsetSwitches(`mcp:${s.name}`) : null,
        instructionBlock(s),
        s.dropped.length ? h("div", { class: "notice warn" }, `Dropped tools: ${s.dropped.map((d) => `${d.tool} (${d.reason})`).join("; ")}`) : null,
        (s.include.length || s.exclude.length) ? h("div", { class: "hint" }, `${s.include.length ? `only: ${s.include.join(", ")}  ` : ""}${s.exclude.length ? `not: ${s.exclude.join(", ")}` : ""}`) : null, result);
    }

    function instructionBlock(s) {
      const n = notes[s.name] || { text: "", enabled: true, undocumented: true };
      const editor = h("div", { hidden: true }, instructionEditor({ scope: `mcp:${s.name}`, text: n.text, enabled: n.enabled, onChange: () => draw() }));
      return h("div", { class: "stack" },
        h("div", { class: "row" }, h("span", { class: "label" }, "What is this for?"), n.undocumented ? badge("undocumented", "warn") : null, !n.enabled && n.text ? badge("not used", "warn") : null,
          button(editor.hidden ? "Edit" : "Close", () => { editor.hidden = !editor.hidden; }, { small: true, iconName: "edit" })),
        n.text ? h("div", { class: "pre" }, n.text) : h("div", { class: "hint" }, n.server_text ? `The server describes itself: ${n.server_text.slice(0, 200)}` : "Nothing written. The agent only sees the tool names, so it may not know when to use them."),
        editor);
    }

    function form(s) {
      const isNew = !s;
      const name = h("input", { type: "text", value: s ? s.name : "", readonly: !isNew || null, placeholder: "linux_mcp", pattern: "[a-z0-9_]{1,24}" });
      const url = h("input", { type: "url", value: s ? s.url : "", placeholder: "http://10.70.0.20:8080/mcp" });
      const verify = h("select", {}, h("option", { value: "true" }, "Verify the certificate"), h("option", { value: "false" }, "Do not verify (self-signed)"), h("option", { value: "ca" }, "Use a CA file…"));
      const ca = h("input", { type: "text", placeholder: "/data/certs/corp-ca.pem", hidden: true });
      if (s && s.verify_tls === false) verify.value = "false"; else if (s && typeof s.verify_tls === "string") { verify.value = "ca"; ca.value = s.verify_tls; ca.hidden = false; }
      verify.addEventListener("change", () => { ca.hidden = verify.value !== "ca"; });
      const headersEl = h("div", { class: "stack" });
      const headerRows = [];
      const addHeader = (hname = "", stored = false) => {
        const n = h("input", { type: "text", value: hname, placeholder: "Authorization" });
        const v = h("input", { type: "password", autocomplete: "off", placeholder: stored ? "unchanged (type to replace)" : "value" });
        const row = h("div", { class: "row" }, n, v, button("", () => { headerRows.splice(headerRows.indexOf(entry), 1); row.remove(); }, { small: true, iconName: "x", title: "Remove" }));
        const entry = { n, v, stored };
        headerRows.push(entry); headersEl.append(row);
      };
      for (const hn of (s ? s.headers : [])) addHeader(hn, true);
      const timeout = h("input", { type: "number", min: "1", value: s ? s.timeout : "", placeholder: "120" });
      const connect = h("input", { type: "number", min: "1", value: s ? s.connect_timeout : "", placeholder: "15" });
      const maxTools = h("input", { type: "number", min: "1", value: s ? s.max_tools : "", placeholder: "40" });
      const include = h("input", { type: "text", value: s ? s.include.join(", ") : "", placeholder: "only these tools (comma separated)" });
      const exclude = h("input", { type: "text", value: s ? s.exclude.join(", ") : "", placeholder: "never these tools" });
      const proto = h("select", {}, ["auto", "2026-07-28", "2025-11-25", "2025-06-18", "2025-03-26"].map((p) => h("option", { value: p }, p))); proto.value = s ? s.protocol_version : "auto";
      const enabled = h("input", { type: "checkbox" }); enabled.checked = s ? s.enabled : true;
      const skip = h("input", { type: "checkbox" });
      const note = notes[s ? s.name : ""] || { text: "", enabled: true };
      const draftServer = () => {
        if (!name.value.trim() || !url.value.trim()) { toast("Fill in the name and the URL first: the draft is based on the tools the server offers", "err"); return null; }
        const out = { name: name.value.trim(), url: url.value.trim(), verify_tls: verify.value === "ca" ? ca.value.trim() : verify.value === "true" };
        const hs = {};
        for (const r of headerRows) if (r.n.value.trim()) hs[r.n.value.trim()] = r.v.value || (r.stored ? null : "");
        if (Object.keys(hs).length) out.headers = hs;
        return out;
      };
      const editor = instructionEditor({ scope: `mcp:${s ? s.name : name.value.trim() || "new"}`, text: note.text, enabled: note.enabled, standalone: false, draftServer: s ? null : draftServer, rows: 5 });
      const list = (t) => t.split(",").map((x) => x.trim()).filter(Boolean);
      const submit = h("button", { class: "btn btn-primary", type: "submit" }, "Test and save");
      return h("form", { class: "item stack", onsubmit: async (e) => {
        e.preventDefault();
        submit.disabled = true;
        const headers = {};
        for (const r of headerRows) if (r.n.value.trim()) headers[r.n.value.trim()] = r.v.value || (r.stored ? null : "");
        const bodyOut = { url: url.value.trim(), enabled: enabled.checked, headers, verify_tls: verify.value === "ca" ? ca.value.trim() : verify.value === "true",
          timeout: timeout.value ? Number(timeout.value) : null, connect_timeout: connect.value ? Number(connect.value) : null, max_tools: maxTools.value ? Number(maxTools.value) : null,
          include: list(include.value), exclude: list(exclude.value), protocol_version: proto.value, skip_test: skip.checked };
        const written = editor.read().text.trim();
        if (isNew && !written) { toast("What is this for? is required: say when the agent should use this server", "err"); submit.disabled = false; return; }
        if (written) bodyOut.instruction = written;
        const ok = await attempt(() => put(`/api/mcp/servers/${encodeURIComponent(name.value.trim())}`, bodyOut), "Server saved");
        submit.disabled = false;
        if (ok) { editing = null; setTimeout(draw, 300); }
      } },
        h("div", { class: "grid2" }, field("Name", name, "a-z, 0-9 and _; becomes the toolset mcp:<name>"), field("URL", url), field("TLS", verify), field("CA file", ca),
          field("Call timeout (s)", timeout), field("Connect timeout (s)", connect), field("Tool cap", maxTools), field("Protocol", proto), field("Only these tools", include), field("Never these tools", exclude)),
        h("div", { class: "label" }, isNew ? "What is this for? (required)" : "What is this for?"), editor,
        h("div", { class: "label" }, "Headers (values are encrypted and never shown again)"), headersEl, h("div", { class: "row" }, button("Add header", () => addHeader(), { small: true, iconName: "plus" })),
        h("div", { class: "row" }, h("label", { class: "check" }, enabled, "Enabled"), h("label", { class: "check" }, skip, "Save without testing")),
        h("div", { class: "row" }, submit, button("Cancel", () => { editing = null; draw(); })));
    }

    await draw();
  },
};
