// System status, the account (password, login sessions) and API keys for the OpenAI-compatible API.
import { ago, attempt, badge, busy, button, bytes, clear, copyText, dangerButton, del, duration, empty, field, get, h, panel, post, skeleton, state, toast } from "../core.js";

function stats(items) {
  return h("div", { class: "stats" }, items.map(([value, label, cls]) => h("div", { class: "stat" }, h("div", { class: `value ${cls || ""}` }, String(value)), h("div", { class: "label" }, label))));
}

export const systemPage = {
  async render(root, ctx) {
    root.append(skeleton(4));
    const s = await get("/api/status");
    clear(root);
    const tg = s.telegram;
    const tgDot = tg.state === "running" ? "ok" : tg.state === "error" ? "err" : tg.state === "starting" ? "warn" : "off";
    root.append(
      stats([[s.version, "Version"], [duration(s.uptime), "Uptime"], [s.sessions, "Conversations"], [s.messages, "Messages"], [bytes(s.db_bytes), "Database"]]),
      panel("Gateways", "system",
        h("table", {}, h("tbody", {},
          h("tr", {}, h("td", {}, "Telegram"), h("td", {}, h("span", { class: `dot ${tgDot}` }, tg.state === "running" ? `running as @${tg.bot}` : tg.state), tg.open && tg.state !== "stopped" && badge("open to everyone", "warn")),
            h("td", { class: "muted" }, tg.last_error || (tg.last_update ? `last message ${ago(tg.last_update)}` : ""))),
          h("tr", {}, h("td", {}, "Knowledge base"), h("td", {}, s.rag ? h("span", { class: "dot ok" }, "on") : h("span", { class: "dot off" }, "off")), h("td", { class: "muted" }, s.rag ? `${s.rag.chunks ?? ""} passages` : "turn it on in Config")),
          ...s.mcp.map((m) => h("tr", {}, h("td", {}, `MCP · ${m.name}`), h("td", {}, h("span", { class: `dot ${m.state === "connected" ? "ok" : m.state === "error" ? "err" : m.state === "disabled" ? "off" : "warn"}` }, m.state), m.tls_unverified && badge("TLS not verified", "warn")),
            h("td", { class: "muted" }, m.state === "connected" ? `${m.tools} tools · protocol ${m.protocol}` : m.error)))))),
      panel("Storage", "knowledge",
        h("table", {}, h("tbody", {},
          h("tr", {}, h("td", {}, "Data directory"), h("td", { class: "mono" }, s.data_dir)),
          h("tr", {}, h("td", {}, "Secrets backend"), h("td", {}, h("span", { class: `dot ${s.vault.ok ? "ok" : "err"}` }, s.vault.backend), " ", h("span", { class: "muted" }, s.vault.detail)))))));
    ctx.setCount(null);
  },
};

export const accountPage = {
  async render(root, ctx) {
    root.append(skeleton(3));
    const sessions = await get("/api/auth/sessions");
    clear(root);
    const current = h("input", { type: "password", autocomplete: "current-password" });
    const next = h("input", { type: "password", autocomplete: "new-password" });
    const again = h("input", { type: "password", autocomplete: "new-password" });
    const save = h("button", { class: "btn btn-primary", type: "submit" }, "Change password");
    const revoke = (id) => async () => {
      if (await attempt(() => del(`/api/auth/sessions/${id}`), "Signed out")) { clear(root); await accountPage.render(root, ctx); }
    };
    const rows = sessions.map((s) => h("tr", {},
      h("td", {}, (s.user_agent || "unknown").slice(0, 60), s.current && badge("this one", "ok")), h("td", { class: "mono" }, s.ip || "–"), h("td", {}, ago(s.last_seen)),
      h("td", {}, !s.current && dangerButton("Sign out", revoke(s.id)))));
    root.append(
      panel("Password", "account", h("form", { class: "stack", onsubmit: async (e) => {
        e.preventDefault();
        if (next.value !== again.value) { toast("The two new passwords differ", "err"); return; }
        save.disabled = true;
        const ok = await attempt(() => post("/api/auth/password", { current_password: current.value, new_password: next.value }), "Password changed. Other sessions were signed out.");
        save.disabled = false;
        if (ok) { current.value = next.value = again.value = ""; }
      } }, h("div", { class: "grid2" }, field("Current password", current), field("New password", next), field("New password again", again)), h("div", { class: "row" }, save))),
      panel("Signed-in sessions", "sessions", h("table", {}, h("thead", {}, h("tr", {}, ["Browser", "Address", "Last seen", ""].map((x) => h("th", {}, x)))), h("tbody", {}, rows))));
    ctx.setCount(sessions.length);
  },
};

export const keysPage = {
  async render(root, ctx) {
    const shown = h("div", { class: "notice warn", role: "status", hidden: true });
    const listEl = h("div", {});
    const name = h("input", { type: "text", maxlength: "64", placeholder: "for example Open WebUI" });
    const scope = h("select", {}, h("option", { value: "chat" }, "chat: /v1/chat/completions only"), h("option", { value: "admin" }, "admin: also the dashboard API"));
    const expires = h("input", { type: "datetime-local" });
    const create = h("button", { class: "btn btn-primary", type: "submit" }, "Create key");
    const draw = async () => {
      const keys = await get("/api/keys");
      ctx.setCount(keys.filter((k) => !k.revoked).length);
      clear(listEl);
      if (!keys.length) { listEl.append(empty("No API key yet. Create one to use the OpenAI-compatible API.")); return; }
      listEl.append(h("table", {}, h("thead", {}, h("tr", {}, ["Name", "Scope", "Created", "Last used", "Expires", ""].map((x) => h("th", {}, x)))), h("tbody", {}, keys.map((k) =>
        h("tr", {}, h("td", {}, k.name, k.revoked && badge("revoked", "err")), h("td", {}, badge(k.scope, k.scope === "admin" ? "warn" : "")), h("td", {}, ago(k.created)), h("td", {}, k.last_used ? ago(k.last_used) : "never"),
          h("td", {}, k.expires ? new Date(k.expires * 1000).toLocaleString() : "never"),
          h("td", {}, !k.revoked && dangerButton("Revoke", async () => { if (await attempt(() => del(`/api/keys/${k.id}`), "Key revoked")) draw(); })))))));
    };
    root.append(
      shown,
      panel("New key", "keys", h("form", { class: "stack", onsubmit: async (e) => {
        e.preventDefault();
        create.disabled = true;
        const body = { name: name.value.trim(), scope: scope.value };
        if (expires.value) body.expires_at = new Date(expires.value).toISOString();
        const made = await attempt(() => post("/api/keys", body));
        create.disabled = false;
        if (!made) return;
        name.value = ""; expires.value = "";
        clear(shown); shown.hidden = false;
        shown.append(h("div", {}, "Copy this key now. It is shown only once."), h("code", { class: "pre" }, made.key), button("Copy", () => copyText(made.key), { small: true, iconName: "copy" }));
        draw();
      } }, h("div", { class: "grid2" }, field("Name", name), field("Scope", scope), field("Expires (optional)", expires)), h("div", { class: "row" }, create))),
      panel("Keys", "keys", listEl),
      panel("Use it", "chat", h("p", { class: "pre" }, `curl ${location.origin}/v1/chat/completions \\\n  -H "Authorization: Bearer <key>" -H "Content-Type: application/json" \\\n  -d '{"model":"default","messages":[{"role":"user","content":"Hello"}]}'`)));
    await draw();
  },
};
