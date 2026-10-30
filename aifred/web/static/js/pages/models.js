// Models: providers (with keys and models), routes with failover order, health and usage (spec 6.1).
import { ago, attempt, badge, busy, button, clear, dangerButton, del, empty, field, get, h, icon, number, panel, post, put, skeleton, toast } from "../core.js";

const modelPath = (id) => id.split("/").map(encodeURIComponent).join("/");

function stateTag(state) {
  const kind = state === "healthy" ? "ok" : state === "cooldown" ? "warn" : "err";
  return badge(state, kind);
}

export default {
  async render(root, ctx) {
    const open = new Set();          // provider ids whose model table is expanded
    let showAdd = false;
    let days = 7;
    const body = h("div", {});
    root.append(body);

    async function load() {
      const [providers, presets, routes, health] = await Promise.all([get("/api/providers"), get("/api/providers/presets"), get("/api/routes"), get("/api/llm/health")]);
      const models = {};
      await Promise.all(providers.map(async (p) => { models[p.id] = await get(`/api/providers/${p.id}/models`); }));
      const usage = await get(`/api/usage?days=${days}`);
      const rag = await get("/api/rag/status").catch(() => null);
      return { providers, presets, routes, health, models, usage, visionRoute: rag ? rag.vision.route : "vision" };
    }

    async function redraw() {
      let data;
      try { data = await load(); ctx.banner(""); } catch (err) { if (err.status !== 401) ctx.banner(err.message); return; }
      clear(body);
      ctx.setCount(data.providers.length);
      body.append(gettingStarted(data), providersPanel(data), routesPanel(data), healthPanel(data));
    }

    // Getting started --------------------------------------------------------------------------

    function gettingStarted(data) {
      const hasProvider = data.providers.length > 0;
      const tested = Object.values(data.models).flat().some((m) => m.test_ok && m.kind !== "embedding");
      const route = data.routes.find((r) => r.name === "default");
      const hasRoute = !!route && route.entries.length > 0;
      if (hasProvider && tested && hasRoute) return h("span");
      const step = (done, text) => h("div", { class: `step${done ? " done" : ""}` }, icon(done ? "check" : "plus"), " ", text);
      return panel("Getting started", "models",
        h("div", { class: "steps" },
          step(hasProvider, "1. Add a provider: pick a preset below, paste the API key (none for a local Ollama) and save. The connection is tested first."),
          step(tested, "2. Open the provider's models and press Test on one: it must answer a tool-calling request before it can be used."),
          step(hasRoute, "3. Put that model on the default route. Chat uses it from then on."),
        ));
    }

    // Providers --------------------------------------------------------------------------------

    function providersPanel(data) {
      const wrap = panel("Providers", "models");
      const head = wrap.querySelector(".panel-head");
      head.append(button(showAdd ? "Close" : "Add provider", () => { showAdd = !showAdd; redraw(); }, { primary: !showAdd, small: true, iconName: showAdd ? "x" : "plus" }));
      const bodyEl = wrap.querySelector(".panel-body");
      if (showAdd) bodyEl.append(addForm(data));
      if (!data.providers.length && !showAdd) bodyEl.append(empty("No provider yet.", button("Add the first provider", () => { showAdd = true; redraw(); }, { primary: true })));
      for (const p of data.providers) bodyEl.append(providerItem(p, data));
      return wrap;
    }

    function addForm(data) {
      const preset = h("select", {}, data.presets.map((p) => h("option", { value: p.type }, p.label)));
      const slug = h("input", { type: "text", placeholder: "short name, for example groq", pattern: "[a-z0-9][a-z0-9_\\-]{0,31}" });
      const base = h("input", { type: "url", placeholder: "https://…" });
      const keyIn = h("input", { type: "password", autocomplete: "off", placeholder: "API key" });
      const extra = h("input", { type: "text", placeholder: "Cloudflare account id" });
      const verify = h("select", {}, h("option", { value: "true" }, "Verify the certificate"), h("option", { value: "false" }, "Do not verify (self-signed)"));
      const skip = h("input", { type: "checkbox" });
      const note = h("p", { class: "hint" });
      const extraField = field("Account id", extra);
      const sync = () => {
        const p = data.presets.find((x) => x.type === preset.value);
        base.value = p.base_url; note.textContent = p.notes || "";
        keyIn.disabled = !p.key && false; keyIn.placeholder = p.key ? "API key" : "API key (optional)";
        extraField.hidden = !p.config_fields.includes("account_id");
        if (!slug.value || data.presets.some((x) => x.type === slug.value)) slug.value = data.providers.some((x) => x.slug === p.type) ? "" : p.type;
      };
      preset.addEventListener("change", sync); sync();
      const submit = h("button", { class: "btn btn-primary", type: "submit" }, "Test and save");
      return h("form", { class: "item stack", onsubmit: async (e) => {
        e.preventDefault();
        submit.disabled = true;
        const p = data.presets.find((x) => x.type === preset.value);
        const config = {}; if (!extraField.hidden && extra.value) config.account_id = extra.value.trim();
        const headers = Object.fromEntries(Object.entries(p.headers || {}).filter(([, v]) => v));
        const result = await attempt(() => post("/api/providers", {
          slug: slug.value.trim(), type: p.type, base_url: base.value.trim(), verify_tls: verify.value === "true", config, headers,
          api_key: keyIn.value || null, skip_test: skip.checked,
        }), "Provider saved");
        submit.disabled = false;
        if (result) { showAdd = false; open.add(result.id); redraw(); }
      } },
        h("div", { class: "grid2" }, field("Type", preset), field("Name", slug, "Letters, digits, - and _"), field("Base URL", base), field("API key", keyIn, "Stored encrypted, never shown again"), extraField, field("TLS", verify)),
        note,
        h("label", { class: "check" }, skip, "Save without testing the connection"),
        h("div", { class: "row" }, submit));
    }

    function providerItem(p, data) {
      const models = data.models[p.id] || [];
      const item = h("div", { class: "item" });
      const result = h("span", { class: "muted" });
      const refresh = button("Refresh models", null, { small: true, iconName: "refresh" });
      refresh.onclick = busy(refresh, async () => {
        const rows = await attempt(() => post(`/api/providers/${p.id}/models/refresh`), "Model list refreshed");
        if (rows) { open.add(p.id); redraw(); }
      });
      const test = button("Test connection", null, { small: true });
      test.onclick = busy(test, async () => {
        const r = await attempt(() => post(`/api/providers/${p.id}/test`));
        if (r) toast(r.ok ? `Connected, ${r.models} models listed` : `Failed: ${r.error}`, r.ok ? "ok" : "err");
      });
      const toggle = button(p.enabled ? "Disable" : "Enable", async () => { if (await attempt(() => put(`/api/providers/${p.id}`, { enabled: !p.enabled }))) redraw(); }, { small: true });
      const editBtn = button("Edit", () => { editing.hidden = !editing.hidden; }, { small: true, iconName: "edit" });
      item.append(h("div", { class: "item-head" },
        h("span", { class: "title" }, p.slug), badge(p.type), !p.enabled && badge("disabled", "warn"), p.tls_unverified && badge("TLS not verified", "warn"),
        test, refresh, toggle, editBtn,
        dangerButton("Delete", async () => { if (await attempt(() => del(`/api/providers/${p.id}`), "Provider deleted")) redraw(); })),
        h("div", { class: "muted mono" }, p.base_url), result);

      // edit form
      const base = h("input", { type: "url", value: p.base_url });
      const verify = h("select", {}, h("option", { value: "true" }, "Verify the certificate"), h("option", { value: "false" }, "Do not verify (self-signed)"));
      verify.value = p.verify_tls === false ? "false" : "true";
      const daily = h("input", { type: "number", min: "1", value: p.budget_tokens_daily ?? "", placeholder: "no limit" });
      const monthly = h("input", { type: "number", min: "1", value: p.budget_tokens_monthly ?? "", placeholder: "no limit" });
      const editing = h("form", { class: "stack", hidden: true, onsubmit: async (e) => {
        e.preventDefault();
        const ok = await attempt(() => put(`/api/providers/${p.id}`, {
          base_url: base.value.trim(), verify_tls: typeof p.verify_tls === "string" && verify.value === "true" ? p.verify_tls : verify.value === "true",
          budget_tokens_daily: daily.value ? Number(daily.value) : null, budget_tokens_monthly: monthly.value ? Number(monthly.value) : null,
        }), "Provider updated");
        if (ok) redraw();
      } }, h("div", { class: "grid2" }, field("Base URL", base), field("TLS", verify), field("Daily token budget", daily), field("Monthly token budget", monthly)),
        h("div", { class: "row" }, h("button", { class: "btn btn-primary btn-sm", type: "submit" }, "Save")));
      item.append(editing);

      // keys
      const keysEl = h("div", { class: "stack" });
      for (const k of p.keys) {
        const replace = h("input", { type: "password", autocomplete: "off", placeholder: "new value", hidden: true });
        const apply = button("Save", async () => {
          if (await attempt(() => put(`/api/providers/${p.id}/keys/${k.id}`, { value: replace.value }), "Key replaced")) redraw();
        }, { small: true });
        apply.hidden = true;
        keysEl.append(h("div", { class: "row" },
          h("span", { class: "mono" }, `key ${k.label || k.id}  ••••${k.last4 || ""}`), badge(k.status, k.status === "ok" ? "ok" : "err"),
          button("Replace", () => { replace.hidden = !replace.hidden; apply.hidden = replace.hidden; }, { small: true }), replace, apply,
          dangerButton("Delete", async () => { if (await attempt(() => del(`/api/providers/${p.id}/keys/${k.id}`), "Key deleted")) redraw(); })));
      }
      const newKey = h("input", { type: "password", autocomplete: "off", placeholder: "Add an API key" });
      keysEl.append(h("div", { class: "row" }, newKey, button("Add key", async () => {
        if (newKey.value && await attempt(() => post(`/api/providers/${p.id}/keys`, { value: newKey.value }), "Key added")) redraw();
      }, { small: true, iconName: "plus" })));
      item.append(h("div", { class: "label" }, "Keys"), keysEl);

      // models
      const tbl = h("div", { hidden: !open.has(p.id) });
      const toggleModels = button(`Models (${models.length})`, () => { tbl.hidden = !tbl.hidden; tbl.hidden ? open.delete(p.id) : open.add(p.id); }, { small: true, iconName: "down" });
      item.append(h("div", { class: "row" }, toggleModels), tbl);
      tbl.append(modelsTable(p, models));
      return item;
    }

    // The price per million tokens turns the Usage page's token counts into a cost; empty means no price (local models, free tiers).
    function priceInputs(p, m) {
      const make = (placeholder, value) => h("input", { class: "price", type: "number", min: "0", step: "any", placeholder, "aria-label": `Price ${placeholder} per million tokens`, value: value ?? "" });
      const pin = make("in", m.price_in); const pout = make("out", m.price_out);
      const save = async () => {
        const num = (el) => (el.value.trim() === "" ? null : Number(el.value));
        if (await attempt(() => put(`/api/providers/${p.id}/models`, [{ model_id: m.model_id, price_in: num(pin), price_out: num(pout) }]), "Price saved")) redraw();
      };
      pin.addEventListener("change", save); pout.addEventListener("change", save);
      return [pin, pout];
    }

    function modelsTable(p, models) {
      const rows = models.map((m) => {
        const t = button(m.kind === "embedding" ? "Test embedding" : "Test", null, { small: true });
        t.onclick = busy(t, async () => {
          const path = `/api/providers/${p.id}/models/${modelPath(m.model_id)}/${m.kind === "embedding" ? "embedding-test" : "test"}`;
          const r = await attempt(() => post(path));
          if (r) { toast(r.ok ? `${m.model_id}: OK${r.dim ? `, ${r.dim} dimensions` : ""}` : `${m.model_id}: ${r.error}`, r.ok ? "ok" : "err"); redraw(); }
        });
        const eyes = button("Test vision", null, { small: true, title: "Show the model a picture and check that it reads it" });
        eyes.onclick = busy(eyes, async () => {
          const r = await attempt(() => post(`/api/providers/${p.id}/models/${modelPath(m.model_id)}/vision-test`));
          if (r) { toast(r.ok ? `${m.model_id} reads images` : `${m.model_id}: ${r.error}`, r.ok ? "ok" : "err"); redraw(); }
        });
        const status = m.test_ok === true ? badge(m.kind === "embedding" ? `ok · ${m.dim}d` : "tools ok", "ok") : m.test_ok === false ? badge("failed", "err") : badge("not tested");
        return h("tr", {}, h("td", { class: "mono" }, m.model_id, !m.available && badge("gone", "warn")), h("td", {}, m.kind), h("td", { class: "mono" }, m.context_window ? number(m.context_window) : "–"),
          h("td", { class: "row" }, ...priceInputs(p, m)),
          h("td", {}, status, m.test_error && h("div", { class: "hint" }, m.test_error)), h("td", {}, m.vision && m.vision_tested ? badge("reads images", "ok") : m.vision_tested ? badge("cannot read", "err") : m.vision ? badge("claims vision") : "–", m.vision_error && h("div", { class: "hint" }, m.vision_error)), h("td", { class: "row" }, t, m.kind === "embedding" ? null : eyes));
      });
      const idIn = h("input", { type: "text", placeholder: "model id, for example llama3.1:8b" });
      const add = h("div", { class: "row" }, idIn, button("Add manually", async () => {
        if (idIn.value.trim() && await attempt(() => put(`/api/providers/${p.id}/models`, [{ model_id: idIn.value.trim() }]), "Model added")) { open.add(p.id); redraw(); }
      }, { small: true, iconName: "plus" }));
      return h("div", { class: "stack" },
        rows.length ? h("table", {}, h("thead", {}, h("tr", {}, ["Model", "Kind", "Context", "Price per 1M tokens (USD, in / out)", "Test", "Vision", ""].map((x) => h("th", {}, x)))), h("tbody", {}, rows)) : h("p", { class: "muted" }, "No models listed. Use Refresh models, or add one by hand."),
        add);
    }

    // Routes -----------------------------------------------------------------------------------

    function routesPanel(data) {
      const wrap = panel("Routes", "journey");
      const bodyEl = wrap.querySelector(".panel-body");
      bodyEl.append(h("p", { class: "hint" }, "A route is an ordered list of models. The first that answers wins; the next ones are the fallback. Only models that passed the tool-calling test can be added."));
      const choices = [], eyes = [];
      for (const p of data.providers) for (const m of data.models[p.id] || []) {
        if (m.test_ok && m.kind !== "embedding") choices.push({ provider: p.slug, model: m.model_id });
        if (m.vision && m.vision_tested && m.kind !== "embedding") eyes.push({ provider: p.slug, model: m.model_id });
      }
      bodyEl.append(h("p", { class: "hint" }, `The ${data.visionRoute} route reads images and scanned pages for the knowledge base. It has no default: put a model that reads images (Test vision) first and any paid provider last. Images go only to the models on this route.`));
      for (const r of data.routes) bodyEl.append(routeItem(r, r.name === data.visionRoute ? eyes : choices));
      const name = h("input", { type: "text", placeholder: "new route name", pattern: "[a-z0-9_\\-]{1,32}" });
      bodyEl.append(h("div", { class: "row" }, name, button("Create route", async () => {
        if (name.value.trim() && await attempt(() => put(`/api/routes/${encodeURIComponent(name.value.trim())}`, { entries: [] }), "Route created")) redraw();
      }, { small: true, iconName: "plus" })));
      return wrap;
    }

    function routeItem(route, choices) {
      let draft = route.entries.map((e) => ({ provider: e.provider, model: e.model, key_id: e.key_id, timeouts: e.timeouts }));
      const item = h("div", { class: "item" });
      const list = h("div", {});
      const save = button("Save order", null, { primary: true, small: true });
      const result = h("div", { class: "notice", hidden: true });
      const changed = () => JSON.stringify(draft.map((e) => [e.provider, e.model])) !== JSON.stringify(route.entries.map((e) => [e.provider, e.model]));
      const drawEntries = () => {
        clear(list);
        draft.forEach((e, i) => list.append(h("div", { class: "entry" },
          h("span", { class: "mono muted" }, String(i + 1)), h("span", { class: "mono" }, `${e.provider} / ${e.model}`),
          h("span", { class: "row" },
            button("", () => { if (i) { [draft[i - 1], draft[i]] = [draft[i], draft[i - 1]]; drawEntries(); } }, { small: true, iconName: "up", title: "Move up", disabled: i === 0 }),
            button("", () => { if (i < draft.length - 1) { [draft[i + 1], draft[i]] = [draft[i], draft[i + 1]]; drawEntries(); } }, { small: true, iconName: "down", title: "Move down", disabled: i === draft.length - 1 }),
            button("", () => { draft.splice(i, 1); drawEntries(); }, { small: true, iconName: "x", title: "Remove" })))));
        if (!draft.length) list.append(h("p", { class: "muted" }, "No model on this route yet."));
        save.disabled = !changed();
      };
      const select = h("select", {}, h("option", { value: "" }, choices.length ? "Add a model…" : "No tested model yet"), choices.map((c, i) => h("option", { value: String(i) }, `${c.provider} / ${c.model}`)));
      select.addEventListener("change", () => {
        if (select.value === "") return;
        const c = choices[Number(select.value)];
        draft.push({ provider: c.provider, model: c.model, key_id: null, timeouts: {} }); select.value = ""; drawEntries();
      });
      save.onclick = busy(save, async () => { if (await attempt(() => put(`/api/routes/${encodeURIComponent(route.name)}`, { entries: draft }), "Route saved")) redraw(); });
      const test = button("Test route", null, { small: true });
      test.onclick = busy(test, async () => {
        const r = await attempt(() => post(`/api/routes/${encodeURIComponent(route.name)}/test`));
        if (!r) return;
        result.hidden = false; result.className = `notice ${r.ok ? "ok" : "err"}`; clear(result);
        result.append(r.ok ? `Answered by ${r.entry} in ${r.latency_ms} ms: ${r.reply}` : `Failed: ${r.error}`, ...(r.failures || []).map((f) => h("div", { class: "hint" }, `${f.entry}: ${f.reason}`)), ...(r.notices || []).map((n) => h("div", { class: "hint" }, n)));
      });
      item.append(h("div", { class: "item-head" }, h("span", { class: "title" }, route.name), test,
        dangerButton("Delete", async () => { if (await attempt(() => del(`/api/routes/${encodeURIComponent(route.name)}`), "Route deleted")) redraw(); })),
        list, h("div", { class: "row" }, select, save), result);
      drawEntries();
      return item;
    }

    // Health and usage -------------------------------------------------------------------------

    function healthPanel(data) {
      const wrap = panel("Health and usage", "logs");
      const bodyEl = wrap.querySelector(".panel-body");
      if (!data.health.length) { bodyEl.append(empty("Nothing to show until a provider exists.")); return wrap; }
      const rows = data.health.map((p) => h("tr", {},
        h("td", { class: "mono" }, p.provider, !p.enabled && badge("disabled", "warn")),
        h("td", { class: "mono" }, `${number(p.tokens_today)} tok · ${p.calls_today} calls · ${p.errors_today} errors · ${p.fallbacks_today} fallbacks`),
        h("td", { class: "mono" }, p.budget_tokens_daily ? `${number(p.budget_tokens_daily)} / day` : "no limit"),
        h("td", {}, p.entries.length ? p.entries.map((e) => h("div", { class: "row" }, stateTag(e.state), h("span", { class: "mono" }, `${e.route}: ${e.model}`),
          e.state !== "healthy" && button("Reset", async () => { if (await attempt(() => post(`/api/llm/health/${e.entry_id}/reset`), "Reset")) redraw(); }, { small: true }),
          e.reason && h("span", { class: "hint" }, e.reason))) : h("span", { class: "muted" }, "not on any route")),
        h("td", { class: "mono" }, p.last_error ? `${p.last_error.detail || p.last_error.error || JSON.stringify(p.last_error)}` : "–")));
      bodyEl.append(h("table", {}, h("thead", {}, h("tr", {}, ["Provider", "Today", "Budget", "Routes", "Last error"].map((x) => h("th", {}, x)))), h("tbody", {}, rows)));

      const seg = h("div", { class: "segmented", role: "tablist", "aria-label": "Period" }, [1, 7, 30].map((d) =>
        h("button", { type: "button", role: "tab", "aria-selected": String(d === days), onclick: () => { days = d; redraw(); } }, d === 1 ? "Today" : `${d} days`)));
      const usageRows = data.usage.map((u) => h("tr", {}, h("td", { class: "mono" }, u.day), h("td", { class: "mono" }, `${u.provider} / ${u.model}`), h("td", {}, u.agent || "–"),
        h("td", { class: "mono" }, String(u.calls)), h("td", { class: "mono" }, String(u.errors)), h("td", { class: "mono" }, `${number(u.tokens_in || 0)} / ${number(u.tokens_out || 0)}`), h("td", { class: "mono" }, u.avg_latency_ms ? `${u.avg_latency_ms} ms` : "–")));
      bodyEl.append(h("div", { class: "row between" }, h("span", { class: "label" }, "Usage"), seg),
        usageRows.length ? h("table", {}, h("thead", {}, h("tr", {}, ["Day", "Model", "Agent", "Calls", "Errors", "Tokens in / out", "Latency"].map((x) => h("th", {}, x)))), h("tbody", {}, usageRows)) : h("p", { class: "muted" }, "No calls in this period."));
      return wrap;
    }

    body.append(skeleton(4));
    await redraw();
  },
};
