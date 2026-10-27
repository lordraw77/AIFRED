// A row of switches that turns a toolset on or off for each gateway, so nobody edits TOOLSETS by hand.
import { attempt, clear, get, h, put } from "./core.js";

const NAMES = { CLI: "CLI", WEB: "Web", API: "API", TELEGRAM: "Telegram", CRON: "Scheduled" };

/**
 * @param {string} name toolset name, e.g. "agents" or "mcp:linux_ssh"
 * @param {{onChange?: () => void}} options called after a change was saved
 * @returns {HTMLElement} a container that fills itself once the current state is loaded
 */
export function toolsetSwitches(name, { onChange } = {}) {
  const url = `/api/toolsets/${encodeURIComponent(name).replace(/%3A/gi, ":")}`;
  const box = h("div", { class: "gw-switch", role: "group", "aria-label": `Where ${name} is on` });
  const draw = (state) => {
    clear(box);
    box.append(h("span", { class: "muted" }, "Use in:"));
    for (const [gateway, on] of Object.entries(state.gateways)) {
      box.append(h("button", {
        class: "btn btn-sm", type: "button", "aria-pressed": String(on),
        title: on ? `On for ${NAMES[gateway] || gateway}: click to turn off` : `Off for ${NAMES[gateway] || gateway}: click to turn on`,
        onclick: async () => {
          const next = await attempt(() => put(url, { gateways: [gateway], on: !on }), `${name} ${on ? "off" : "on"} for ${NAMES[gateway] || gateway}`);
          if (next) { draw(next); if (onChange) onChange(); }
        },
      }, NAMES[gateway] || gateway));
    }
    const all = Object.values(state.gateways).every(Boolean);
    box.append(h("button", {
      class: "btn btn-sm", type: "button", title: all ? "Turn off for every gateway" : "Turn on for every gateway",
      onclick: async () => {
        const next = await attempt(() => put(url, { gateways: Object.keys(state.gateways), on: !all }), `${name} ${all ? "off" : "on"} everywhere`);
        if (next) { draw(next); if (onChange) onChange(); }
      },
    }, all ? "Off everywhere" : "On everywhere"));
  };
  attempt(() => get(url)).then((state) => { if (state) draw(state); });
  return box;
}
