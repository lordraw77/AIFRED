// Guide: the bilingual user guide, one section per page of the dashboard, with screenshots (Markdown files under /static/guide).
import { clear, empty, h, panel, skeleton, store } from "../core.js";
import { renderMarkdown } from "../md.js";

const LANGS = [["en", "English"], ["it", "Italiano"]];
const LABELS = { en: { sections: "Sections", missing: "This section is not written yet.", failed: "The guide could not be loaded.", prev: "Previous", next: "Next" },
  it: { sections: "Sezioni", missing: "Questa sezione non è ancora scritta.", failed: "Non è stato possibile caricare la guida.", prev: "Precedente", next: "Successiva" } };

function defaultLanguage() {
  const saved = store("guide.lang");
  if (saved === "en" || saved === "it") return saved;
  return (navigator.language || "en").toLowerCase().startsWith("it") ? "it" : "en";
}

export default {
  async render(root, ctx) {
    const query = new URLSearchParams(location.hash.split("?")[1] || "");
    let lang = LANGS.some(([code]) => code === query.get("l")) ? query.get("l") : defaultLanguage();
    root.append(skeleton(4));
    let index;
    try {
      const response = await fetch("/static/guide/index.json", { credentials: "same-origin" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      index = (await response.json()).sections;
    } catch (err) { clear(root); root.append(empty(LABELS[lang].failed)); return; }
    clear(root);
    ctx.setCount(index.length);
    let current = index.some((s) => s.id === query.get("s")) ? query.get("s") : index[0].id;
    const list = h("div", { class: "chat-list", role: "list" });
    const article = h("div", { class: "guide" });
    const tabs = h("div", { class: "segmented", role: "tablist", "aria-label": "Language" });
    const cache = new Map();

    const remember = () => history.replaceState(null, "", `#/guide?s=${current}&l=${lang}`);
    const drawList = () => {
      clear(list);
      for (const s of index) {
        list.append(h("button", { class: "list-item", type: "button", role: "listitem", "aria-current": s.id === current ? "true" : null, onclick: () => { current = s.id; remember(); drawList(); drawArticle(); } },
          h("span", { class: "s-title" }, s.title[lang]), h("span", { class: "s-meta" }, s.summary[lang])));
      }
    };
    const drawTabs = () => {
      clear(tabs);
      for (const [code, name] of LANGS) tabs.append(h("button", { type: "button", role: "tab", "aria-selected": String(code === lang), lang: code, onclick: () => { lang = code; store("guide.lang", code); remember(); drawTabs(); drawList(); drawArticle(); } }, name));
    };
    async function load(id, language) {
      const key = `${language}/${id}`;
      if (!cache.has(key)) {
        const response = await fetch(`/static/guide/${key}.md`, { credentials: "same-origin" });
        cache.set(key, response.ok ? await response.text() : null);
      }
      return cache.get(key);
    }
    async function drawArticle() {
      const id = current, language = lang;
      clear(article); article.append(skeleton(3));
      let text = null;
      try { text = await load(id, language); } catch (err) { text = null; }
      if (id !== current || language !== lang) return;
      clear(article);
      if (text === null) { article.append(empty(LABELS[language].missing)); return; }
      const at = index.findIndex((s) => s.id === id);
      article.append(renderMarkdown(text, { links: true }),
        h("nav", { class: "guide-nav row between", "aria-label": "Guide" },
          at > 0 ? h("a", { class: "btn btn-sm", href: `#/guide?s=${index[at - 1].id}&l=${language}` }, `← ${LABELS[language].prev}: ${index[at - 1].title[language]}`) : h("span", {}),
          at < index.length - 1 ? h("a", { class: "btn btn-sm", href: `#/guide?s=${index[at + 1].id}&l=${language}` }, `${LABELS[language].next}: ${index[at + 1].title[language]} →`) : h("span", {})));
      root.scrollTop = 0;
      const main = document.getElementById("content");
      if (main) main.scrollTop = 0;
    }

    root.append(h("div", { class: "split" }, h("div", { class: "stack" }, h("div", { class: "row between" }, h("span", { class: "label" }, LABELS[lang].sections), tabs), list), article));
    drawTabs(); drawList(); await drawArticle();
  },
};
