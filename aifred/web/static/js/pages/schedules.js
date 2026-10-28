// Schedules: recurring tasks the agent runs on its own, with the result delivered on Telegram.
import { toolsetSwitches } from "../toolsets.js";
import { attempt, badge, busy, button, clear, dangerButton, del, empty, field, get, h, panel, post, put, skeleton, ago } from "../core.js";

const STATUS = { ok: "ok", error: "err", running: "warn", missed: "warn" };

export default {
  async render(root, ctx) {
    let editing = null;      // task name being edited, "" for a new one
    let opened = null;       // task whose history is open
    const body = h("div", {});
    root.append(body);

    async function draw() {
      const data = await get("/api/schedules");
      ctx.setCount(data.tasks.length);
      clear(body);
      const head = panel("Scheduled tasks", "schedules");
      head.querySelector(".panel-head").append(button("New task", () => { editing = ""; draw(); }, { primary: true, small: true, iconName: "plus" }));
      const inner = head.querySelector(".panel-body");
      if (!data.enabled) inner.append(h("div", { class: "notice warn" }, "The scheduler is off (SCHEDULER_ENABLED on the Config page): nothing runs on its own."));
      else if (!data.running) inner.append(h("div", { class: "notice warn" }, "The scheduler clock is not running."));
      if (!data.telegram_ids.length) inner.append(h("div", { class: "hint" }, "No Telegram id is on TELEGRAM_ALLOWED_IDS, so results are kept here but cannot be delivered."));
      if (!data.tasks.length && editing === null) inner.append(empty("No scheduled task yet. A task is a prompt the agent runs by itself, for example a news summary every morning."));
      if (editing === "") inner.append(form(null, data));
      for (const t of data.tasks) inner.append(editing === t.name ? form(t, data) : card(t));
      body.append(head, h("div", { class: "stack" }, h("div", { class: "label" }, "Let the assistant list, run, pause and propose tasks (toolset cron)"), toolsetSwitches("cron")), h("p", { class: "hint" }, `Times use ${data.timezone || "the server's time zone"} (SCHEDULER_TIMEZONE). Runs are unattended and use only the toolsets switched on in the Scheduled column of the Tools page. The model can propose tasks in a chat, but they wait in Journey for your approval.`));
    }

    function card(t) {
      const runBtn = button("Run now", null, { small: true });
      runBtn.onclick = busy(runBtn, async () => { if (await attempt(() => post(`/api/schedules/${encodeURIComponent(t.name)}/run`), "Started")) setTimeout(draw, 1500); });
      const log = h("div", { class: "stack", hidden: opened !== t.name });
      if (opened === t.name) loadRuns(t.name, log);
      return h("div", { class: "item" },
        h("div", { class: "item-head" }, h("span", { class: `dot ${t.enabled ? STATUS[t.last_status] || "off" : "off"}` }), h("span", { class: "title" }, t.name),
          badge(t.expression), !t.enabled && badge("paused", "warn"), t.running && badge("running", "warn"), t.created_by === "model" && badge("proposed by the model"),
          runBtn,
          button(t.enabled ? "Pause" : "Resume", async () => { if (await attempt(() => put(`/api/schedules/${encodeURIComponent(t.name)}`, { enabled: !t.enabled }))) draw(); }, { small: true }),
          button("History", () => { opened = opened === t.name ? null : t.name; draw(); }, { small: true }),
          button("Edit", () => { editing = t.name; draw(); }, { small: true, iconName: "edit" }),
          dangerButton("Delete", async () => { if (await attempt(() => del(`/api/schedules/${encodeURIComponent(t.name)}`), "Task deleted")) draw(); })),
        h("div", { class: "pre" }, t.prompt),
        h("div", { class: "hint" }, `${t.enabled ? `next ${t.next_run_text}` : "paused"} · last ${t.last_run ? ago(t.last_run) : "never"}${t.last_status ? ` (${t.last_status})` : ""} · ${t.run_count} runs · ${t.deliver_chat !== null ? `delivered to Telegram ${t.deliver_chat}` : "not delivered"}`),
        log);
    }

    async function loadRuns(name, into) {
      into.hidden = false;
      into.append(skeleton(2));
      const runs = await get(`/api/schedules/${encodeURIComponent(name)}/runs`);
      clear(into);
      if (!runs.length) into.append(h("div", { class: "hint" }, "No run yet."));
      for (const r of runs) {
        into.append(h("div", { class: "item" },
          h("div", { class: "item-head" }, badge(r.status, STATUS[r.status] || ""), h("span", { class: "muted" }, ago(r.started)), r.delivered ? badge("delivered", "ok") : null, r.session_id ? h("span", { class: "mono muted" }, r.session_id) : null),
          r.error ? h("div", { class: "notice err" }, r.error) : null,
          r.output ? h("div", { class: "pre" }, r.output) : null));
      }
    }

    function form(t, data) {
      const isNew = !t;
      const name = h("input", { type: "text", value: t ? t.name : "", readonly: !isNew || null, placeholder: "morning-news", pattern: "[a-z0-9][a-z0-9_\\-]{0,39}", maxlength: "40" });
      const expression = h("input", { type: "text", value: t ? t.expression : "", placeholder: "every morning at 6   ·   ogni lunedì alle 9   ·   0 8 * * 1-5   ·   every 30m" });
      const prompt = h("textarea", { rows: "4", maxlength: "2000", placeholder: "Search the web for today's news about Python and write five short bullet points." }, t ? t.prompt : "");
      const chat = h("select", {}, h("option", { value: "" }, "Do not deliver (keep the result here)"), ...data.telegram_ids.map((id) => h("option", { value: String(id) }, `Telegram ${id}`)));
      chat.value = t && t.deliver_chat !== null ? String(t.deliver_chat) : (isNew && data.telegram_ids.length ? String(data.telegram_ids[0]) : "");
      const preview = h("div", { class: "hint" });
      let timer = null;
      const check = () => {
        clearTimeout(timer);
        timer = setTimeout(async () => {
          if (!expression.value.trim()) { preview.textContent = ""; return; }
          try {
            const r = await post("/api/schedules/preview", { expression: expression.value });
            preview.textContent = `${r.source ? `Understood as ${r.interpreted}. ` : ""}Next: ${r.runs.join("  ·  ")}`;
            preview.className = "hint";
          } catch (e) { preview.textContent = e.message; preview.className = "notice err"; }
        }, 300);
      };
      expression.addEventListener("input", check);
      if (t) check();
      const save = button("Save", null, { primary: true });
      save.onclick = busy(save, async () => {
        const values = { expression: expression.value, prompt: prompt.value, deliver_chat: chat.value === "" ? null : Number(chat.value) };
        if (await attempt(() => put(`/api/schedules/${encodeURIComponent(name.value.trim())}`, values), "Saved")) { editing = null; draw(); }
      });
      return h("div", { class: "item stack" },
        field("Name", name), field("When", expression, "Write it as a sentence in English or Italian (every morning at 6 · ogni lunedì e giovedì alle 9 · every 2 hours · il primo del mese alle 8), as five cron fields (minute hour day month weekday), as an alias (@hourly, @daily, @weekly) or as 'every 30m'. Not more often than every " + data.min_interval + " min."), preview,
        field("What to do", prompt, "Write it as a complete instruction: nobody can answer questions while it runs."), field("Deliver the result to", chat),
        h("div", { class: "row" }, save, button("Cancel", () => { editing = null; draw(); })));
    }

    await draw();
    const timer = setInterval(() => { if (editing === null && !document.hidden) draw(); }, 15000);
    return () => clearInterval(timer);
  },
};
