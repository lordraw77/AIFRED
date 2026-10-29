// Connectors: ready-made MCP servers of well-known products, set up in a few steps (the next step is Authorize).
import { toolsetSwitches } from "../toolsets.js";
import { attempt, badge, button, clear, copyText, dangerButton, del, disclosure, field, get, h, post, put, skeleton, toast } from "../core.js";

const POLL_MS = 3000;
const POLL_FOR_MS = 180000;

function stateBadge(c) {
  if (c.conflict) return badge("name taken", "warn");
  if (!c.configured) return badge("not set up");
  if (!c.authorized) return badge("needs sign-in", "warn");
  if (c.state === "connected") return badge(`connected · ${c.tools} tools`, "ok");
  return badge(c.state || "signed in", c.state === "error" ? "err" : "");
}

export default {
  async render(root, ctx) {
    const openSteps = new Set();      // connectors whose steps are open, kept across redraws
    const body = h("div", {}, skeleton(3));
    let timer = null;
    let first = true;
    root.append(h("p", { class: "hint" }, "A connector is a ready-made MCP server of a product you use. Register an app at the product, paste its client id and secret, choose what the assistant may do and sign in. After that it works like any MCP server: its tools appear on the Tools page, where you switch them on for the gateways you want."), body);

    async function draw(list) {
      const data = list || await get("/api/connectors");
      ctx.setCount(data.filter((c) => c.configured).length);
      if (first) { for (const c of data) if (!c.configured) openSteps.add(`${c.id}:steps`); first = false; }   // the steps start open for what is not set up yet
      clear(body);
      for (const c of data) body.append(card(c));
    }

    function stopPolling() { if (timer) { clearInterval(timer); timer = null; } }

    // after Authorize: look every few seconds until the sign-in is done, so the page does not need a reload
    function waitForSignIn(id) {
      stopPolling();
      const until = Date.now() + POLL_FOR_MS;
      timer = setInterval(async () => {
        const data = await attempt(() => get("/api/connectors"));
        const c = data && data.find((x) => x.id === id);
        if (c && c.authorized) { stopPolling(); toast("Signed in"); draw(data); } else if (Date.now() > until) stopPolling();
      }, POLL_MS);
    }

    function card(c) {
      const clientId = h("input", { type: "text", value: c.client ? c.client.client_id : "", placeholder: "123456-abc.apps.googleusercontent.com", "aria-label": `${c.title} client id`, autocomplete: "off" });
      const secret = h("input", { type: "password", placeholder: c.client && c.client.has_secret ? "saved (leave empty to keep it)" : "client secret", "aria-label": `${c.title} client secret`, autocomplete: "off" });
      const chosen = new Set(c.client ? c.client.scope.split(" ").filter(Boolean) : c.permissions.filter((p) => p.default).map((p) => p.id));
      const boxes = c.permissions.map((p) => {
        const box = h("input", { type: "checkbox", checked: chosen.has(p.id) || null, "aria-label": p.label });
        box.addEventListener("change", () => { if (box.checked) chosen.add(p.id); else chosen.delete(p.id); });
        return h("label", { class: "check perm" }, box, h("span", {}, h("strong", {}, p.label), " ", h("span", { class: "muted" }, p.detail)));
      });
      const save = button("Save", null, { primary: true, small: true });
      save.onclick = async () => {
        save.disabled = true;
        try {
          const saved = await attempt(() => put(`/api/connectors/${encodeURIComponent(c.id)}`, { client_id: clientId.value, client_secret: secret.value || null, scopes: [...chosen] }), "Saved");
          if (saved) draw();
        } finally { save.disabled = false; }
      };
      const authorize = c.configured ? button(c.authorized ? "Sign in again" : "Authorize", async () => {
        const r = await attempt(() => post(`/api/mcp/servers/${encodeURIComponent(c.id)}/oauth/start`));
        if (r) { window.open(r.url, "_blank", "noopener"); toast("Finish signing in in the new tab; this page updates by itself"); waitForSignIn(c.id); }
      }, { small: true, primary: !c.authorized }) : null;
      const forget = c.authorized ? button("Forget the sign-in", async () => { if (await attempt(() => post(`/api/mcp/servers/${encodeURIComponent(c.id)}/oauth/forget`), "Signed out")) draw(); }, { small: true }) : null;
      const remove = c.configured ? dangerButton("Remove", async () => { if (await attempt(() => del(`/api/connectors/${encodeURIComponent(c.id)}`), `${c.title} removed`)) draw(); }) : null;

      const redirect = h("div", { class: "row" }, h("code", { class: "redirect" }, c.redirect_uri), button("Copy", () => copyText(c.redirect_uri), { small: true, iconName: "copy" }));
      const steps = disclosure(`${c.id}:steps`, openSteps,
        h("span", { class: "label" }, "How to set it up"),
        () => [h("ol", { class: "steps-list" }, c.steps.map((s) => h("li", {}, s))), c.docs ? h("a", { href: c.docs, target: "_blank", rel: "noopener noreferrer" }, "The product's own guide") : null]);
      return h("div", { class: "item stack" },
        h("div", { class: "item-head" }, h("span", { class: "title" }, c.title), stateBadge(c), authorize, forget, remove),
        h("div", {}, c.description),
        c.conflict ? h("div", { class: "notice warn" }, `An MCP server named ${c.id} already exists with another address. Rename or delete it on the MCP page before setting this connector up.`) : null,
        c.error ? h("div", { class: "notice err" }, c.error) : null,
        steps,
        h("div", { class: "label" }, "Redirect address to register"), redirect,
        c.redirect_warning ? h("div", { class: "notice warn" }, c.redirect_warning) : null,
        field("Client id", clientId), field("Client secret", secret),
        h("div", { class: "label" }, "What the assistant may do"), h("div", { class: "stack perms" }, boxes),
        c.notes.map((n) => h("div", { class: "hint" }, n)),
        h("div", { class: "row" }, save),
        c.configured ? h("div", { class: "stack" }, h("div", { class: "label" }, `Let the assistant use ${c.title} (its tools are the toolset mcp:${c.id})`), toolsetSwitches(`mcp:${c.id}`)) : null);
    }

    await draw();
    return () => stopPolling();
  },
};
