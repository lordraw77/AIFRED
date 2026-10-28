// Library: answers and results kept on purpose. The assistant finds them again when a request matches; each item can be
// edited, copied, rewritten from the request that produced it (a draft you accept or discard) and restored.
import { ago, attempt, badge, busy, button, clear, copyText, dangerButton, del, empty, field, get, h, panel, post, put, skeleton } from "../core.js";
import { renderMarkdown } from "../md.js";
import { toolsetSwitches } from "../toolsets.js";

const SOURCES = { chat: "from a chat", agent: "from an agent", manual: "written here" };

export default {
  async render(root, ctx) {
    root.append(skeleton(4));
    let query = "", tag = "", openId = null, data = await get("/api/library");
    const view = h("div", {});
    clear(root); root.append(view);

    const reload = async () => { data = await get(`/api/library?q=${encodeURIComponent(query)}&tag=${encodeURIComponent(tag)}`); draw(); };

    function itemForm(item, done) {
      const title = h("input", { type: "text", maxlength: "120", value: item?.title || "" });
      const tags = h("input", { type: "text", value: (item?.tags || []).join(", "), placeholder: "tags, comma separated" });
      const prompt = h("textarea", { rows: 2, maxlength: "4000", placeholder: "The request that produces this text, so it can be written again (optional)" }); prompt.value = item?.prompt || "";
      const content = h("textarea", { rows: 10, placeholder: "Markdown" }); content.value = item?.content || "";
      const save = button(item ? "Save" : "Add", null, { primary: true, small: true });
      save.onclick = busy(save, async () => {
        const body = { title: title.value.trim(), content: content.value, prompt: prompt.value, tags: tags.value.split(",") };
        const saved = await attempt(() => (item ? put(`/api/library/${item.id}`, body) : post("/api/library", body)), item ? "Saved" : "Added");
        if (saved) { openId = saved.id; await reload(); }
      });
      return h("div", { class: "stack" }, field("Title", title), field("Tags", tags), field("Request (used to regenerate)", prompt), field("Text", content),
        h("div", { class: "row" }, save, button("Cancel", done, { small: true })));
    }

    function detail(summary) {
      const box = h("div", { class: "lib-detail" }, skeleton(2));
      (async () => {
        const item = await attempt(() => get(`/api/library/${summary.id}`));
        if (!item) return;
        const body = h("div", { class: "rich lib-body" }, renderMarkdown(item.content, { links: true, breaks: true }));
        const drafts = h("div", {});
        const show = () => {
          clear(box);
          box.append(body, drafts, h("div", { class: "row wrap" }, ...actions(item)));
          if (item.prompt) box.append(h("div", { class: "hint" }, `Request: ${item.prompt.slice(0, 300)}`));
        };
        const regenerate = (btn) => busy(btn, async () => {
          clear(drafts); drafts.append(skeleton(3));
          const r = await attempt(() => post(`/api/library/${item.id}/regenerate`));
          clear(drafts);
          if (!r) return;
          const use = button("Use this version", null, { primary: true, small: true });
          use.onclick = busy(use, async () => { if (await attempt(() => put(`/api/library/${item.id}`, { content: r.draft }), "Replaced; the old text can be restored")) { openId = item.id; await reload(); } });
          drafts.append(h("div", { class: "draft" }, h("strong", {}, "New version (not saved yet)"), h("div", { class: "rich" }, renderMarkdown(r.draft, { links: true, breaks: true })),
            h("div", { class: "row wrap" }, use, button("Discard", () => clear(drafts), { small: true }))));
        });
        function actions(it) {
          const regen = button("Regenerate", null, { small: true, title: it.prompt ? "Ask again with the stored request and compare" : "Add the request first (Edit)" });
          regen.onclick = regenerate(regen);
          const out = [
            button("Copy", () => copyText(it.content), { small: true }),
            button("Edit", () => { clear(box); box.append(itemForm(it, show)); }, { small: true }),
            regen,
          ];
          if (it.has_previous) {
            const back = button("Restore previous", null, { small: true, title: "Swap with the version before the last change" });
            back.onclick = busy(back, async () => { if (await attempt(() => post(`/api/library/${it.id}/restore`), "Restored")) { openId = it.id; await reload(); } });
            out.push(back);
          }
          out.push(dangerButton("Delete", async () => { if (await attempt(() => del(`/api/library/${it.id}`), "Deleted")) { openId = null; await reload(); } }));
          return out;
        }
        show();
      })();
      return box;
    }

    function draw() {
      clear(view);
      const search = h("input", { type: "search", placeholder: "Search the library", "aria-label": "Search", value: query });
      let timer = 0;
      search.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(() => { query = search.value; reload().then(() => { const el = view.querySelector("input[type=search]"); if (el) { el.focus(); el.setSelectionRange(query.length, query.length); } }); }, 250); });
      const tagRow = h("div", { class: "row wrap" }, ...data.tags.map((t) => h("button", { class: "btn btn-sm", type: "button", "aria-pressed": String(t === tag), onclick: () => { tag = t === tag ? "" : t; reload(); } }, t)));
      const list = h("div", {});
      for (const it of data.items) {
        const head = h("button", { class: "lib-head", type: "button", "aria-expanded": String(it.id === openId), onclick: () => { openId = openId === it.id ? null : it.id; draw(); } },
          h("span", { class: "lib-title" }, it.title), ...it.tags.map((t) => badge(t)), h("span", { class: "muted" }, `${SOURCES[it.source] || it.source} · ${ago(it.updated)}`));
        list.append(h("div", { class: "item" }, head, it.id === openId ? detail(it) : h("div", { class: "hint lib-preview" }, it.preview.replace(/[#*_`>]/g, " ").slice(0, 200))));
      }
      if (!data.items.length) list.append(empty(query || tag ? "Nothing matches." : "Nothing saved yet. Press Save to library under an answer in the chat, or add an item here."));
      const adder = h("div", {});
      const add = button("New item", () => { clear(adder); adder.append(itemForm(null, () => clear(adder))); }, { primary: true, small: true, iconName: "plus" });
      view.append(panel("Library", "library",
        h("div", { class: "row between" }, h("span", { class: "mono" }, `${data.count} / ${data.limit} items`), add), adder,
        toolsetSwitches("library"), h("div", { class: "stack" }, h("div", { class: "row" }, search), data.tags.length ? tagRow : null), list));
      ctx.setCount(data.count);
    }
    draw();
  },
};
