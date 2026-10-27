// The "What is this for?" editor: a text, the description assistant (Generate, Improve, Shorten, Test, Refine) whose drafts are only shown (nothing is saved until the admin accepts) and Save.
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

  let tested = null;       // {text, score} of the last evaluation, so the score is journaled with exactly that text
  const draft = async (mode, trigger) => {
    const server = draftServer ? draftServer() : null;
    if (draftServer && !server) return;
    const body = { scope, hint: hint.value.trim(), mode, current: area.value };
    if (server) body.server = server;
    const r = await attempt(() => post("/api/instructions/suggest", body));
    if (!r) return;
    clear(draftBox); draftBox.hidden = false;
    draftBox.append(
      h("div", { class: "label" }, `${mode === "generate" ? "Draft" : mode === "improve" ? "Improved text" : "Shortened text"} from ${r.tools} tools, written by ${r.provider} / ${r.model} (the tool names and descriptions were sent there)`),
      h("div", { class: "pre" }, r.draft),
      ...r.warnings.map((w) => h("div", { class: "hint" }, `⚠ ${w}`)),
      h("div", { class: "row" },
        button("Use this text", () => { area.value = r.draft; update(); draftBox.hidden = true; area.focus(); }, { primary: true, small: true }),
        button("Discard", () => { draftBox.hidden = true; }, { small: true })));
  };
  const assistButton = (label, mode, title) => {
    const b = button(label, null, { small: true, title });
    b.onclick = busy(b, () => draft(mode, b));
    return b;
  };
  const generate = assistButton("Generate", "generate", "Ask the model for a draft from the tools it offers");
  const improve = assistButton("Improve", "improve", "Ask the model to rewrite the text above, keeping what is right");
  const shorten = assistButton("Shorten", "shorten", "Ask the model to cut the text above without losing when to use it");

  const resultBox = h("div", { class: "notice", hidden: true });
  const evaluateWith = async (rounds, trigger) => {
    if (!area.value.trim()) { toast("Write or generate a text first", "err"); return; }
    clear(resultBox); resultBox.hidden = false;
    resultBox.append(h("div", { class: "hint" }, rounds ? "Testing and refining: the model is called many times, this can take a few minutes…" : "Testing: each request is sent to the model with the real tools; no tool is run…"));
    const r = await attempt(() => post("/api/assist/evaluate", { scope, text: area.value, rounds, hint: hint.value.trim() }));
    clear(resultBox);
    if (!r) { resultBox.hidden = true; return; }
    const score = `${r.score[0]}/${r.score[1]}`;
    tested = { text: r.text.trim(), score };
    resultBox.append(
      h("div", { class: "label" }, `Score ${score}${r.baseline ? ` (the saved text scores ${r.baseline[0]}/${r.baseline[1]})` : ""}${r.rounds ? ` after ${r.rounds} refinement round${r.rounds > 1 ? "s" : ""}` : ""} · ${r.provider} / ${r.model}`),
      h("table", {}, h("thead", {}, h("tr", {}, ["Request", "Should", "Chose", ""].map((x) => h("th", {}, x)))),
        h("tbody", {}, r.cases.map((c) => h("tr", {}, h("td", {}, c.request), h("td", { class: "mono" }, c.expect === "this" ? "use it" : c.expect === "other" ? "another tool" : "no tool"),
          h("td", { class: "mono" }, c.error || c.chosen.join(", ") || "no tool"), h("td", {}, badge(c.passed ? "pass" : "fail", c.passed ? "ok" : "err")))))),
      ...(r.text.trim() !== area.value.trim() ? [h("div", {}, h("div", { class: "label" }, "Refined text"), h("div", { class: "pre" }, r.text),
        button("Use the refined text", () => { area.value = r.text; update(); area.focus(); }, { primary: true, small: true }))] : []),
      h("div", { class: "hint" }, "Nothing is saved until you press Save."));
  };
  const test = button("Test", null, { small: true, title: "Dry-run the model's tool choice with this text; no tool is executed" });
  test.onclick = busy(test, () => evaluateWith(0));
  const refine = button("Refine and test", null, { small: true, title: "Test, then let the model rewrite the text up to 3 times and keep the best" });
  refine.onclick = busy(refine, () => evaluateWith(3));

  const save = button("Save", null, { primary: true, small: true });
  save.onclick = busy(save, async () => {
    if (!area.value.trim()) { toast("Write something first, or delete the instruction", "err"); return; }
    if (await attempt(() => put(`/api/instructions/${scope}`, { text: area.value, enabled: on.checked, score: tested && tested.text === area.value.trim() ? tested.score : "" }), "Instruction saved")) onChange(true);
  });
  const remove = button("Delete", async () => { if (await attempt(() => del(`/api/instructions/${scope}`), "Instruction deleted")) { area.value = ""; update(); onChange(false); } }, { small: true });

  const node = h("div", { class: "stack" },
    area, h("div", { class: "row between" }, counter, h("label", { class: "check" }, on, "Use it in the prompt")),
    h("div", { class: "row" }, hint, generate, improve, shorten, draftServer ? null : test, draftServer ? null : refine), draftBox, resultBox,
    standalone ? h("div", { class: "row" }, save, text ? remove : null) : null);
  node.read = () => ({ text: area.value, enabled: on.checked });
  return node;
}

export function undocumentedTag() { return badge("undocumented", "warn"); }
