// Improve / Shorten buttons for the texts that steer a sub-agent or a skill. The draft is only shown; the admin uses or discards it.
import { attempt, busy, button, clear, h, post, toast } from "./core.js";

/**
 * @param {object} o
 * @param {string} o.kind  agent.description, agent.role or skill.description
 * @param {() => string} o.getText
 * @param {(text: string) => void} o.setText
 * @param {() => string} [o.getContext] facts the draft may be based on (never secrets)
 * @param {boolean} [o.shorten] offer Shorten as well
 */
export function assistButtons({ kind, getText, setText, getContext = () => "", shorten = false }) {
  const box = h("div", { class: "notice", hidden: true });
  const run = (mode) => async () => {
    const text = getText();
    if (mode !== "generate" && !text.trim()) return;
    const r = await attempt(() => post("/api/assist/improve", { kind, mode, text, context: getContext() }));
    if (!r) return;
    clear(box); box.hidden = false;
    box.append(
      h("div", { class: "label" }, `Draft written by ${r.provider} / ${r.model} (the text and context were sent there)`),
      h("div", { class: "pre" }, r.draft),
      ...r.warnings.map((w) => h("div", { class: "hint" }, `⚠ ${w}`)),
      h("div", { class: "row" }, button("Use this text", () => { setText(r.draft); box.hidden = true; }, { primary: true, small: true }), button("Discard", () => { box.hidden = true; }, { small: true })));
  };
  const make = (label, mode, title) => { const b = button(label, null, { small: true, title }); b.onclick = busy(b, run(mode)); return b; };
  const row = h("div", { class: "row" }, make("Generate", "generate", "Write it from the context"), make("Improve", "improve", "Rewrite the text keeping what is right"), shorten ? make("Shorten", "shorten", "Cut it without losing when it applies") : null);
  return h("div", { class: "stack" }, row, box);
}

/**
 * A box that drafts a whole skill or sub-agent from a plain description. The draft is handed to `onDraft` to fill the editor; nothing is saved.
 * @param {object} o
 * @param {"skill"|"agent"} o.kind
 * @param {string} o.label
 * @param {string} o.placeholder
 * @param {(r: {draft: object, provider: string, model: string, warnings: string[]}) => void} o.onDraft
 */
export function generatorBox({ kind, label, placeholder, onDraft }) {
  const text = h("textarea", { rows: "4", maxlength: "1500", placeholder });
  const go = button("Generate", null, { primary: true, small: true });
  go.onclick = busy(go, async () => {
    if (text.value.trim().length < 10) { toast("Describe it in at least a sentence", "err"); return; }
    const r = await attempt(() => post("/api/assist/generate", { kind, description: text.value }));
    if (r) onDraft(r);
  });
  return h("div", { class: "item stack" }, h("div", { class: "label" }, label), text, h("div", { class: "row" }, go),
    h("div", { class: "hint" }, "Your description is sent to the assist model (or the default route). The editor opens with the draft; nothing is saved until you press Save."));
}

/** The line shown above an editor opened from a generated draft. */
export function draftNote(r) {
  return h("div", { class: "notice" }, `Draft written by ${r.provider} / ${r.model}. Read it through and edit it, then Save.`, ...r.warnings.map((w) => h("div", { class: "hint" }, `⚠ ${w}`)));
}
