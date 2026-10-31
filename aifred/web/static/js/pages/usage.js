// Usage: tokens, calls and cost per provider and model, and tokens per day (spec 6.1). Cost needs a price on the model (Models page).
import { badge, clear, empty, get, h, number, panel, skeleton } from "../core.js";

const money = (c) => (c === null || c === undefined ? "–" : `$${c > 0 && c < 0.01 ? c.toFixed(4) : c.toFixed(2)}`);

export default {
  async render(root, ctx) {
    let days = 7;
    const open = new Set();          // providers whose models are expanded
    const openDays = new Set();      // days whose providers are expanded
    const body = h("div", {});
    root.append(body);

    async function redraw() {
      let data;
      try { data = await get(`/api/usage/overview?days=${days}`); ctx.banner(""); } catch (err) { if (err.status !== 401) ctx.banner(err.message); return; }
      clear(body);
      ctx.setCount(data.providers.length);
      const seg = h("div", { class: "segmented", role: "tablist", "aria-label": "Period" }, [1, 7, 30].map((d) =>
        h("button", { type: "button", role: "tab", "aria-selected": String(d === days), onclick: () => { days = d; redraw(); } }, d === 1 ? "Last 24 hours" : `${d} days`)));
      body.append(h("div", { class: "row pad-b" }, seg));
      if (!data.providers.length) { body.append(empty("No model calls in this period.")); return; }
      body.append(totals(data), providersPanel(data), byDayPanel(data), dailyPanel(data));
    }

    function totals(data) {
      const t = data.totals;
      const stat = (value, label, hint) => h("div", { class: "stat" }, h("div", { class: "value" }, String(value)), h("div", { class: "label" }, label), hint && h("div", { class: "hint" }, hint));
      return h("div", { class: "stats" },
        stat(number(t.tokens_in + t.tokens_out), "Tokens", `${number(t.tokens_in)} in · ${number(t.tokens_out)} out`),
        stat(number(t.calls), "Calls", t.errors ? `${t.errors} failed` : "none failed"),
        stat(money(t.cost), "Cost", t.cost === null ? "no price set on any model" : t.unpriced_models ? `${t.unpriced_models} model${t.unpriced_models === 1 ? "" : "s"} without a price` : "all models priced"));
    }

    function providersPanel(data) {
      const rows = [];
      for (const p of data.providers) {
        const isOpen = open.has(p.provider);
        rows.push(h("tr", { class: "row-hover", onclick: () => { if (isOpen) open.delete(p.provider); else open.add(p.provider); redraw(); }, style: "cursor: pointer" },
          h("td", { class: "mono" }, `${isOpen ? "▾" : "▸"} ${p.provider}`), h("td", { class: "mono" }, number(p.calls)), h("td", { class: "mono" }, number(p.errors)),
          h("td", { class: "mono" }, number(p.tokens_in)), h("td", { class: "mono" }, number(p.tokens_out)),
          h("td", { class: "mono" }, money(p.cost), p.cost === null && badge("no price", "warn"))));
        if (isOpen) for (const m of p.models) {
          rows.push(h("tr", { class: "sub" }, h("td", { class: "mono hint" }, `   ${m.model}`), h("td", { class: "mono" }, number(m.calls)), h("td", { class: "mono" }, number(m.errors)),
            h("td", { class: "mono" }, number(m.tokens_in)), h("td", { class: "mono" }, number(m.tokens_out)),
            h("td", { class: "mono" }, money(m.cost), m.cost !== null && h("span", { class: "hint" }, ` ($${m.price_in ?? 0} in · $${m.price_out ?? 0} out per 1M)`))));
        }
      }
      return panel("By provider", "models",
        h("table", {}, h("thead", {}, h("tr", {}, ["Provider", "Calls", "Errors", "Tokens in", "Tokens out", "Cost"].map((x) => h("th", {}, x)))), h("tbody", {}, rows)),
        h("p", { class: "hint" }, "Click a provider for its models. Cost is tokens times the price per million tokens set on the model (Models page, in US dollars). Local models and free tiers need no price."));
    }

    function byDayPanel(data) {
      const cells = (x) => [h("td", { class: "mono" }, number(x.calls)), h("td", { class: "mono" }, number(x.errors)), h("td", { class: "mono" }, number(x.tokens_in)),
        h("td", { class: "mono" }, number(x.tokens_out)), h("td", { class: "mono" }, money(x.cost))];
      const rows = [];
      for (const d of data.by_day) {
        const isOpen = openDays.has(d.day);
        rows.push(h("tr", { class: "row-hover", onclick: () => { if (isOpen) openDays.delete(d.day); else openDays.add(d.day); redraw(); }, style: "cursor: pointer" },
          h("td", { class: "mono" }, `${isOpen ? "▾" : "▸"} ${d.day}`), ...cells(d)));
        if (isOpen) for (const p of d.providers) rows.push(h("tr", { class: "sub" }, h("td", { class: "mono hint" }, `   ${p.provider}`), ...cells(p)));
      }
      return panel("By day", "logs",
        rows.length ? h("table", {}, h("thead", {}, h("tr", {}, ["Day", "Calls", "Errors", "Tokens in", "Tokens out", "Cost"].map((x) => h("th", {}, x)))), h("tbody", {}, rows)) : h("p", { class: "muted" }, "Nothing in this period."),
        h("p", { class: "hint" }, "Days with usage, newest first. Click a day for its providers. Days are UTC."));
    }

    function fill(pct) {   // the CSP blocks inline style attributes, so the width goes through a CSS variable
      const bar = h("div", { class: "bar" }, h("span", {}));
      bar.firstChild.style.setProperty("--pct", String(pct));
      return bar;
    }

    function dailyPanel(data) {
      const perDay = new Map();
      for (const r of data.daily) {
        const d = perDay.get(r.day) || { total: 0, parts: [] };
        d.total += r.tokens; d.parts.push(`${r.provider} ${number(r.tokens)}`); perDay.set(r.day, d);
      }
      const max = Math.max(1, ...[...perDay.values()].map((d) => d.total));
      const rows = [...perDay.entries()].sort((a, b) => b[0].localeCompare(a[0])).map(([day, d]) =>
        h("div", { class: "bar-row", title: d.parts.join(" · ") }, h("span", { class: "mono" }, day), fill(Math.max(1, Math.round((d.total / max) * 100))), h("span", { class: "mono" }, number(d.total))));
      return panel("Tokens per day", "logs", rows.length ? h("div", { class: "bars" }, rows) : h("p", { class: "muted" }, "Nothing in this period."));
    }

    body.append(skeleton(3));
    await redraw();
  },
};
