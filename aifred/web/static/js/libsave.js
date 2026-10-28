// "Save to library": a small inline form (title, tags) shared by the chat, the agents and the library page.
import { ApiError, attempt, button, h, post, toast } from "./core.js";

/** A title from a Markdown text: its first heading, else its first line, cut to a sensible length. */
export function titleOf(text, fallback = "") {
  const line = String(text || "").split("\n").map((l) => l.replace(/^#{1,6}\s+/, "").replace(/[*_`>]/g, "").trim()).find(Boolean) || fallback;
  return line.length > 80 ? `${line.slice(0, 79)}…` : line;
}

/**
 * @param {{content: string, prompt?: string, source?: string, sourceRef?: string, title?: string}} item what to keep
 * @param {{onSaved?: (item: object) => void, onCancel?: () => void}} options
 * @returns {HTMLElement} the form
 */
export function saveForm(item, { onSaved, onCancel } = {}) {
  const title = h("input", { type: "text", maxlength: "120", value: item.title || titleOf(item.content, item.prompt), "aria-label": "Title" });
  const tags = h("input", { type: "text", placeholder: "tags, comma separated (optional)", "aria-label": "Tags" });
  const note = h("div", { class: "hint" });
  let replace = false;
  const submit = async () => {
    const body = { title: title.value.trim(), content: item.content, prompt: item.prompt || "", tags: tags.value.split(","), source: item.source || "chat", source_ref: item.sourceRef || "", replace };
    try {
      const saved = await post("/api/library", body);
      toast(`Saved to the library: ${saved.title}`, "ok");
      if (onSaved) onSaved(saved);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        replace = true; save.textContent = "Replace it";
        note.textContent = "The library already has an item with this title. Press again to replace it (the old text is kept for one undo), or change the title.";
      } else toast(err.message, "err");
    }
  };
  const save = button("Save", submit, { primary: true, small: true });
  title.addEventListener("input", () => { replace = false; save.textContent = "Save"; note.textContent = ""; });
  title.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); submit(); } });
  return h("div", { class: "save-form" }, h("div", { class: "row" }, title, tags), h("div", { class: "row" }, save, button("Cancel", () => onCancel && onCancel(), { small: true })), note);
}

/** A "Save to library" button that opens the form under itself. */
export function saveButton(getItem, label = "Save to library") {
  const wrap = h("span", { class: "save-wrap" });
  const open = button(label, () => {
    open.hidden = true;
    const close = () => { form.remove(); open.hidden = false; };
    const form = saveForm(getItem(), { onSaved: () => { form.remove(); open.hidden = false; open.textContent = "Saved ✓"; open.disabled = true; }, onCancel: close });
    wrap.append(form);
    form.querySelector("input").focus();
  }, { small: true, iconName: "library" });
  wrap.append(open);
  return wrap;
}
