// Improve / Shorten buttons for the texts that steer a sub-agent or a skill. The draft is only shown; the admin uses or discards it.
import { attempt, busy, button, clear, h, post } from "./core.js";

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
