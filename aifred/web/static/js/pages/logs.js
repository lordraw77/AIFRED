// Logs: the last 500 process lines with a level filter and live updates over Server-Sent Events.
import { attempt, button, clear, get, h, state, store } from "../core.js";

const LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR"];
const CAP = 500;

export default {
  async render(root, ctx) {
    let level = LEVELS.includes(store("logs.level")) ? store("logs.level") : "INFO";
    let follow = true, paused = false, controller = null;
    const box = h("div", { class: "logbox", role: "log", tabindex: "0", "aria-label": "Log lines" });
    const pick = h("select", { "aria-label": "Level" }, LEVELS.map((l) => h("option", { value: l }, l)));
    pick.value = level;
    const followBox = h("input", { type: "checkbox" }); followBox.checked = true;
    followBox.addEventListener("change", () => { follow = followBox.checked; });
    const pauseBtn = button("Pause", () => { paused = !paused; pauseBtn.lastChild.textContent = paused ? "Resume" : "Pause"; }, { small: true });
    const line = (r) => {
      const time = new Date(r.ts * 1000).toLocaleTimeString();
      return h("div", { class: `logline lv-${r.level}` }, h("span", { class: "muted" }, time + " "), h("span", { class: "lv" }, r.level), h("span", { class: "muted" }, r.logger + ": "), r.message);
    };
    const add = (r) => {
      box.append(line(r));
      while (box.childNodes.length > CAP) box.removeChild(box.firstChild);
      if (follow) box.scrollTop = box.scrollHeight;
    };
    async function start() {
      if (controller) controller.abort();
      controller = new AbortController();
      clear(box);
      const rows = await attempt(() => get(`/api/logs?level=${level}`)) || [];
      rows.forEach(add);
      try {
        const response = await fetch(`/api/logs/stream?level=${level}`, { credentials: "same-origin", signal: controller.signal });
        if (response.status === 401) { state.onUnauthorized(); return; }
        const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = "";
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          let end;
          while ((end = buffer.indexOf("\n\n")) >= 0) {
            const block = buffer.slice(0, end); buffer = buffer.slice(end + 2);
            const data = block.split("\n").filter((l) => l.startsWith("data:")).map((l) => l.slice(5).trim()).join("");
            if (data && !paused) { try { add(JSON.parse(data)); } catch (e) { /* skip a malformed line */ } }
          }
        }
      } catch (err) { if (err.name !== "AbortError") add({ ts: Date.now() / 1000, level: "WARNING", logger: "dashboard", message: `live updates stopped: ${err.message}` }); }
    }
    pick.addEventListener("change", () => { level = pick.value; store("logs.level", level); start(); });
    root.append(h("div", { class: "row pad-b" }, pick, pauseBtn, h("label", { class: "check" }, followBox, "Follow"), button("Clear view", () => clear(box), { small: true })), box);
    ctx.setCount(null);
    start();
    return () => controller && controller.abort();
  },
};
