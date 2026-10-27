// Journey: the approval queue and the timeline of what the agent learned, with undo for memory and skill changes.
import { ago, attempt, badge, button, clear, dangerButton, empty, get, h, panel, post, skeleton } from "../core.js";

const STATUS_KIND = { applied: "ok", pending: "warn", blocked: "err", rejected: "", approved: "ok", undone: "" };

function describePending(row) {
  const p = row.payload;
  if (row.kind === "instruction") return `instruction for ${p.scope}: ${p.text}${p.reason ? ` (why: ${p.reason})` : ""}`;
  if (row.kind === "memory") return `memory ${p.action} (${p.target || "memory"}): ${p.content || p.match || ""}`;
  if (row.kind === "schedule") return `task ${p.action} ${p.name}${p.expression ? ` [${p.expression}]` : ""}${p.prompt ? `: ${p.prompt}` : ""}${p.deliver_chat ? ` → Telegram ${p.deliver_chat}` : ""}`;
  return `skill ${p.name}: ${p.description || ""}`;
}

export default {
  async render(root, ctx) {
    root.append(skeleton(4));
    const body = h("div", {});
    async function draw() {
      const [pending, journal] = await Promise.all([get("/api/pending"), get("/api/journal?limit=200")]);
      ctx.setCount(pending.length || null);
      clear(root); clear(body);
      root.append(body);
      body.append(
        panel("Waiting for approval", "journey", pending.length ? pending.map((p) => h("div", { class: "entry-row" },
          h("div", { class: "text" }, h("div", { class: "mono muted" }, `#${p.id} · ${ago(p.ts)}${p.session_id ? ` · ${p.session_id}` : ""}`), h("div", {}, describePending(p))),
          button("Approve", async () => { if (await attempt(() => post(`/api/pending/${p.id}/approve`), "Approved")) draw(); }, { primary: true, small: true, iconName: "check" }),
          button("Reject", async () => { if (await attempt(() => post(`/api/pending/${p.id}/reject`), "Rejected")) draw(); }, { small: true, iconName: "x" })))
          : empty("Nothing is waiting. With WRITE_APPROVAL on, the agent's memory and skill changes wait here for you.")),
        panel("Timeline", "logs", journal.length ? journal.map((e) => h("div", { class: "timeline-item" },
          h("span", { class: "mono muted" }, new Date(e.ts * 1000).toLocaleString()), h("span", {}, badge(e.kind), " ", h("span", { class: "mono" }, e.action)),
          h("span", {}, e.summary, e.status === "blocked" && e.data && e.data.reasons ? h("div", { class: "hint" }, e.data.reasons) : null),
          h("span", { class: "row" }, badge(e.status, STATUS_KIND[e.status] || ""),
            e.status === "applied" && ["memory", "skill", "instruction"].includes(e.kind) && e.data ? dangerButton("Undo", async () => { if (await attempt(() => post(`/api/journal/${e.id}/undo`), "Undone")) draw(); }) : null)))
          : empty("Nothing yet. Memory saves, skill saves and settings changes are listed here.")));
    }
    await draw();
  },
};
