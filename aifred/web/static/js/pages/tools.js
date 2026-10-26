// Tools: which toolsets are on for each gateway (read-only; change TOOLSETS on the Config page) and how often each tool was called.
import { badge, button, clear, empty, get, h, number, panel, skeleton } from "../core.js";

export default {
  async render(root, ctx) {
    root.append(skeleton(4));
    const data = await get("/api/tools");
    clear(root);
    ctx.setCount(data.toolsets.length);
    const open = new Set();
    const body = h("div", {});
    root.append(h("p", { class: "hint" }, "A switch shows whether a toolset is on for a gateway. Change it with TOOLSETS and TOOLSETS_<GATEWAY> on the Config page. Counts are calls since the server started."), body);

    function draw() {
      clear(body);
      const rows = [];
      for (const ts of data.toolsets) {
        rows.push(h("tr", { class: "row-hover" },
          h("td", {}, h("button", { class: "btn btn-sm", type: "button", "aria-expanded": String(open.has(ts.name)), onclick: () => { open.has(ts.name) ? open.delete(ts.name) : open.add(ts.name); draw(); } }, ts.name), " ", h("span", { class: "muted" }, `${ts.tools.length} tools`)),
          ...data.gateways.map((g) => h("td", {}, h("span", { class: `switch${ts.enabled[g] ? " on" : ""}`, role: "img", "aria-label": ts.enabled[g] ? "on" : "off" }), " ", h("span", { class: "muted mono" }, ts.enabled[g] ? "on" : "off"))),
          h("td", { class: "mono" }, number(ts.calls))));
        if (open.has(ts.name)) {
          rows.push(h("tr", {}, h("td", { colspan: String(data.gateways.length + 2) }, ts.tools.map((t) => h("div", { class: "row" },
            h("span", { class: "mono" }, t.name), t.restricted_ok ? badge("open bot ok") : null, !t.available ? badge("not available now", "warn") : null, h("span", { class: "muted" }, t.description), h("span", { class: "mono" }, `${t.calls} calls`))))));
        }
      }
      body.append(panel("Toolsets", "tools", data.toolsets.length ? h("table", {}, h("thead", {}, h("tr", {}, ["Toolset", ...data.gateways, "Calls"].map((x) => h("th", {}, x)))), h("tbody", {}, rows)) : empty("No tool registered.")));
    }
    draw();
  },
};
