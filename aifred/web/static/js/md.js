// A minimal Markdown renderer for skill previews. It builds DOM nodes and accepts no HTML:
// every piece of text goes through text nodes, and links are not rendered at all.
import { h } from "./core.js";

function inline(text) {
  const out = [];
  const re = /(`[^`]+`|\*\*[^*]+\*\*|\*[^*\s][^*]*\*)/g;
  let last = 0, m;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const tok = m[0];
    if (tok.startsWith("`")) out.push(h("code", {}, tok.slice(1, -1)));
    else if (tok.startsWith("**")) out.push(h("strong", {}, tok.slice(2, -2)));
    else out.push(h("em", {}, tok.slice(1, -1)));
    last = m.index + tok.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

export function renderMarkdown(source) {
  const root = h("div", { class: "md" });
  const lines = source.replace(/\r\n?/g, "\n").split("\n");
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }
    if (/^```/.test(line)) {
      const code = [];
      i++;
      while (i < lines.length && !/^```/.test(lines[i])) code.push(lines[i++]);
      i++;
      root.append(h("pre", {}, h("code", {}, code.join("\n"))));
      continue;
    }
    const heading = line.match(/^(#{1,4})\s+(.*)$/);
    if (heading) { root.append(h(`h${Math.min(heading[1].length + 1, 5)}`, {}, inline(heading[2]))); i++; continue; }
    if (/^\s*([-*]|\d+\.)\s+/.test(line)) {
      const ordered = /^\s*\d+\./.test(line);
      const list = h(ordered ? "ol" : "ul", {});
      while (i < lines.length && /^\s*([-*]|\d+\.)\s+/.test(lines[i])) list.append(h("li", {}, inline(lines[i++].replace(/^\s*([-*]|\d+\.)\s+/, ""))));
      root.append(list);
      continue;
    }
    const para = [];
    while (i < lines.length && lines[i].trim() && !/^(```|#{1,4}\s|\s*([-*]|\d+\.)\s+)/.test(lines[i])) para.push(lines[i++]);
    root.append(h("p", {}, inline(para.join(" "))));
  }
  return root;
}

/** Text with **marked** parts (from the search snippets) as nodes. */
export function marked(snippet) {
  return String(snippet).split("**").map((part, i) => (i % 2 ? h("mark", {}, part) : part));
}
