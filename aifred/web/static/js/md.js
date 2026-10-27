// A minimal Markdown renderer for skill previews and chat. It builds DOM nodes and accepts no HTML:
// every piece of text goes through text nodes. Links are only rendered when asked for (chat), only for
// http(s) addresses, and always open in a new tab without a referrer.
import { h } from "./core.js";

const TOKEN = /(`[^`]+`|\[[^\]]+\]\(https?:\/\/[^\s)]+\)|\*\*[^*]+\*\*|\*[^*\s][^*]*\*|https?:\/\/[^\s)<]+)/g;

function link(label, url) {
  return h("a", { href: url, target: "_blank", rel: "noopener noreferrer nofollow" }, label);
}

function inline(text, links) {
  const out = [];
  let last = 0, m;
  TOKEN.lastIndex = 0;
  while ((m = TOKEN.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const tok = m[0];
    if (tok.startsWith("`")) out.push(h("code", {}, tok.slice(1, -1)));
    else if (tok.startsWith("[")) {
      const at = tok.lastIndexOf("](");
      out.push(links ? link(tok.slice(1, at), tok.slice(at + 2, -1)) : tok);
    } else if (tok.startsWith("http")) {
      const url = tok.replace(/[.,;:!?]+$/, "");
      out.push(links ? link(url, url) : url);
      if (url.length < tok.length) out.push(tok.slice(url.length));
    } else if (tok.startsWith("**")) out.push(h("strong", {}, tok.slice(2, -2)));
    else out.push(h("em", {}, tok.slice(1, -1)));
    last = m.index + tok.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

const LIST = /^\s*([-*]|\d+\.)\s+/;
const ROW = /^\s*\|.*\|\s*$/;
const RULE = /^\s*([-*_])(\s*\1){2,}\s*$/;

function cells(row) { return row.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim()); }

/**
 * Render Markdown (headings, lists, code, quotes, rules, tables, bold, italic, code spans) as a DOM tree.
 *
 * @param {string} source
 * @param {{links?: boolean, breaks?: boolean}} options links: render http(s) links; breaks: keep single line breaks.
 */
export function renderMarkdown(source, { links = false, breaks = false } = {}) {
  const root = h("div", { class: "md" });
  const lines = source.replace(/\r\n?/g, "\n").split("\n");
  const text = (t) => inline(t, links);
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
    if (RULE.test(line)) { root.append(h("hr", {})); i++; continue; }
    const heading = line.match(/^(#{1,6})\s+(.*)$/);
    if (heading) { root.append(h(`h${Math.min(heading[1].length + 1, 5)}`, {}, text(heading[2]))); i++; continue; }
    if (/^\s*>/.test(line)) {
      const quote = [];
      while (i < lines.length && /^\s*>/.test(lines[i])) quote.push(lines[i++].replace(/^\s*>\s?/, ""));
      root.append(h("blockquote", {}, text(quote.join(breaks ? "\n" : " "))));
      continue;
    }
    if (ROW.test(line) && i + 1 < lines.length && /^\s*\|[\s|:-]+\|\s*$/.test(lines[i + 1])) {
      const head = cells(line);
      i += 2;
      const body = h("tbody", {});
      while (i < lines.length && ROW.test(lines[i])) body.append(h("tr", {}, cells(lines[i++]).map((c) => h("td", {}, text(c)))));
      root.append(h("div", { class: "table-wrap" }, h("table", {}, h("thead", {}, h("tr", {}, head.map((c) => h("th", {}, text(c))))), body)));
      continue;
    }
    if (LIST.test(line)) {
      const ordered = /^\s*\d+\./.test(line);
      const list = h(ordered ? "ol" : "ul", {});
      while (i < lines.length && LIST.test(lines[i])) list.append(h("li", {}, text(lines[i++].replace(LIST, ""))));
      root.append(list);
      continue;
    }
    const para = [];
    while (i < lines.length && lines[i].trim() && !/^(```|#{1,6}\s|>)/.test(lines[i]) && !LIST.test(lines[i]) && !RULE.test(lines[i])) para.push(lines[i++]);
    root.append(h("p", {}, text(para.join(breaks ? "\n" : " "))));
  }
  return root;
}

/** Text with **marked** parts (from the search snippets) as nodes. */
export function marked(snippet) {
  return String(snippet).split("**").map((part, i) => (i % 2 ? h("mark", {}, part) : part));
}
