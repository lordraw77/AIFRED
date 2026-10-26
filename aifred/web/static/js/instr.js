// The "What is this for?" editor: a text, a Suggest button that shows a draft (nothing is saved until the admin accepts it) and Save.
import { attempt, badge, button, busy, clear, del, field, h, post, put, toast } from "./core.js";

const SOFT_LIMIT = 600;

/**
 * @param {object} o
 * @param {string} o.scope        mcp:<name>, toolset:<name> or tool:<name>
 * @param {string} o.text         the stored text ("" when none)
 * @param {boolean} o.enabled
 * @param {() => object|null} [o.draftServer]  for a server that is not saved yet: its connection fields, so tools can be read
 * @param {(saved: boolean) => void} [o.onChange]
 * @param {boolean} [o.standalone] show Save/Delete buttons (false when the surrounding form saves the text)
 */
export function instructionEditor({ scope, text, enabled = true, draftServer = null, onChange = () => {}, standalone = true, rows = 4 }) {
  const area = h("textarea", { rows: String(rows), maxlength: "2000", placeholder: "What is it for? When should the agent use it, and when not?", "aria-label": `Instruction for ${scope}` });
  area.value = text || "";
  const on = h("input", { type: "checkbox" }); on.checked = enabled;
  const counter = h("span", { class: "hint" });
  const hint = h("input", { type: "text", placeholder: "Hint for the draft, for example: my local Linux servers", maxlength: "500" });
  const draftBox = h("div", { class: "notice", hidden: true });
  const update = () => { counter.textContent = `${area.value.length} characters`; counter.className = `hint${area.value.length > SOFT_LIMIT ? " over" : ""}`; };
  area.addEventListener("input", update); update();

  const suggest = button("Suggest", null, { small: true, title: "Ask the model for a draft from the tools it offers" });
  suggest.onclick = busy(suggest, async () => {
    const server = draftServer ? draftServer() : null;
    if (draftServer && !server) return;
    const body = { scope, hint: hint.value.trim() };
    if (server) body.server = server;
    const r = await attempt(() => post("/api/instructions/suggest", body));
    if (!r) return;
    clear(draftBox); draftBox.hidden = false;
    draftBox.append(
      h("div", { class: "label" }, `Draft from ${r.tools} tools, written by ${r.provider} / ${r.model} (the tool names and descriptions were sent there)`),
      h("div", { class: "pre" }, r.draft),
      ...r.warnings.map((w) => h("div", { class: "hint" }, `⚠ ${w}`)),
      h("div", { class: "row" },
        button("Use this draft", () => { area.value = r.draft; update(); draftBox.hidden = true; area.focus(); }, { primary: true, small: true }),
        button("Discard", () => { draftBox.hidden = true; }, { small: true })));
  });

  const save = button("Save", null, { primary: true, small: true });
  save.onclick = busy(save, async () => {
    if (!area.value.trim()) { toast("Write something first, or delete the instruction", "err"); return; }
    if (await attempt(() => put(`/api/instructions/${scope}`, { text: area.value, enabled: on.checked }), "Instruction saved")) onChange(true);
  });
  const remove = button("Delete", async () => { if (await attempt(() => del(`/api/instructions/${scope}`), "Instruction deleted")) { area.value = ""; update(); onChange(false); } }, { small: true });

  const node = h("div", { class: "stack" },
    area, h("div", { class: "row between" }, counter, h("label", { class: "check" }, on, "Use it in the prompt")),
    h("div", { class: "row" }, hint, suggest), draftBox,
    standalone ? h("div", { class: "row" }, save, text ? remove : null) : null);
  node.read = () => ({ text: area.value, enabled: on.checked });
  return node;
}

export function undocumentedTag() { return badge("undocumented", "warn"); }
