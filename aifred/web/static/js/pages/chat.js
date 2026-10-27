// Chat: streaming conversation over POST /api/chat (SSE of JSON events), with a list of web conversations.
import { ago, attempt, clear, empty, get, h, icon, number, state, store, toast, uuid } from "../core.js";

const SESSION_KEY = "chat.session";

function newSessionId() { return `web:${uuid()}`; }

function chip(name, args, result) {
  const line = h("span", { class: "chip", role: "button", tabindex: "0" }, `· ${name}(${args})`);
  const out = h("div", { class: "chip-result", hidden: true }, result || "");
  const toggle = () => { out.hidden = !out.hidden; };
  line.addEventListener("click", toggle);
  line.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); } });
  return { line, out, wrap: h("div", {}, line, out) };
}

function shortArgs(raw) {
  try {
    const text = Object.entries(JSON.parse(raw)).map(([k, v]) => `${k}=${JSON.stringify(v)}`).join(", ");
    return text.length > 100 ? text.slice(0, 99) + "…" : text;
  } catch (e) { return String(raw).slice(0, 100); }
}

function renderStored(messages) {
  const nodes = [];
  const pending = new Map();
  for (const m of messages) {
    if (m.role === "user") nodes.push(message("user", m.content));
    else if (m.role === "assistant") {
      if (m.content) nodes.push(message("assistant", m.content));
      for (const call of m.tool_calls || []) {
        const c = chip(call.function.name, shortArgs(call.function.arguments), "");
        pending.set(call.id, c);
        nodes.push(c.wrap);
      }
    } else if (m.role === "tool") {
      const c = pending.get(m.tool_call_id);
      if (c) c.out.textContent = (m.content || "").slice(0, 500);
    }
  }
  return nodes;
}

function message(role, text) {
  return h("div", { class: `msg ${role}` }, h("div", { class: "who" }, role === "user" ? "You" : role === "error" ? "Error" : "AIFRED"), h("div", { class: "body" }, text));
}

async function* events(response) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let end;
    while ((end = buffer.indexOf("\n\n")) >= 0) {
      const block = buffer.slice(0, end);
      buffer = buffer.slice(end + 2);
      const data = block.split("\n").filter((l) => l.startsWith("data:")).map((l) => l.slice(5).replace(/^ /, "")).join("\n");
      if (data) { try { yield JSON.parse(data); } catch (e) { /* ignore a malformed event */ } }
    }
  }
}

export default {
  async render(root, ctx) {
    const asked = new URLSearchParams((location.hash.split("?")[1] || "")).get("s");   // #/chat?s=<id> opens any conversation
    let sessionId = asked || (store(SESSION_KEY) && /^web:[A-Za-z0-9._-]{1,64}$/.test(store(SESSION_KEY)) ? store(SESSION_KEY) : newSessionId());
    let controller = null;
    const list = h("div", { class: "chat-list", role: "list", "aria-label": "Conversations" });
    const log = h("div", { class: "messages", role: "log", "aria-live": "polite" });
    const input = h("textarea", { rows: 1, placeholder: "Message AIFRED. Type / for commands (/help).", "aria-label": "Message", autofocus: true });
    const send = h("button", { class: "btn btn-primary", type: "button" }, icon("send"), h("span", {}, "Send"));
    const stop = h("button", { class: "btn", type: "button", hidden: true }, icon("stop"), h("span", {}, "Stop"));
    const fresh = h("button", { class: "btn btn-sm", type: "button", title: "Start a new conversation" }, icon("plus"), h("span", {}, "New"));

    const scroll = () => { log.scrollTop = log.scrollHeight; };
    const select = async (id) => {
      sessionId = id;
      const readOnly = !id.startsWith("web:");
      if (!readOnly) store(SESSION_KEY, id);
      input.disabled = readOnly; send.disabled = readOnly;
      input.placeholder = readOnly ? `Read-only: this conversation belongs to ${id.split(":")[0]}. Start a new one to chat here.` : "Message AIFRED. Type / for commands (/help).";
      clear(log);
      const rows = await attempt(() => get(`/api/sessions/${encodeURIComponent(id)}/messages`)) || [];
      if (!rows.length) log.append(empty("This conversation is empty. Say something."));
      else log.append(...renderStored(rows));
      scroll(); refreshList();
    };
    const refreshList = async () => {
      const rows = await attempt(() => get("/api/sessions?source=web&limit=50")) || [];
      clear(list);
      if (!rows.length) list.append(h("p", { class: "muted pad" }, "No conversations yet."));
      for (const s of rows) {
        const item = h("button", { class: "s-item", type: "button", "aria-current": s.id === sessionId ? "true" : null, onclick: () => select(s.id) },
          h("span", { class: "s-title" }, s.title || s.preview || "(untitled)"),
          h("span", { class: "s-meta" }, `${s.message_count} msgs · ${ago(s.updated)}`));
        list.append(item);
      }
    };

    const setRunning = (running) => { send.disabled = running; stop.hidden = !running; input.disabled = running; if (!running) input.focus(); };

    const run = async () => {
      const text = input.value.trim();
      if (!text) return;
      input.value = ""; input.style.height = "";
      if (log.querySelector(".empty")) clear(log);
      log.append(message("user", text));
      const answer = message("assistant", "");
      const body = answer.querySelector(".body");
      log.append(answer); scroll();
      controller = new AbortController();
      setRunning(true);
      const tools = new Map();
      let usage = null;
      try {
        const response = await fetch("/api/chat", {
          method: "POST", credentials: "same-origin", signal: controller.signal,
          headers: { "Content-Type": "application/json", "X-CSRF-Token": state.csrf },
          body: JSON.stringify({ session_id: sessionId, message: text }),
        });
        if (response.status === 401) { state.onUnauthorized(); return; }
        if (!response.ok) {
          const err = await response.json().catch(() => ({}));
          throw new Error(typeof err.detail === "string" ? err.detail : `Request failed (HTTP ${response.status})`);
        }
        for await (const ev of events(response)) {
          if (ev.type === "token") body.textContent += ev.text;
          else if (ev.type === "reset") body.textContent = "";
          else if (ev.type === "tool_call") {
            const c = chip(ev.name, shortArgs(ev.arguments), "…");
            tools.set(ev.name, c);
            answer.before(c.wrap);
          } else if (ev.type === "tool_result") {
            const c = tools.get(ev.name);
            if (c) c.out.textContent = (ev.content || "").slice(0, 500);
          } else if (ev.type === "notice") toast(ev.text);
          else if (ev.type === "command") {
            answer.remove();
            log.append(message("assistant", ev.text));
            if (ev.session_id) { await select(ev.session_id); }
            return;
          } else if (ev.type === "done") {
            if (!body.textContent) body.textContent = ev.content || "";
            usage = ev;
          } else if (ev.type === "error") {
            answer.className = "msg error"; answer.querySelector(".who").textContent = "Error";
            body.textContent = ev.message;
          }
          scroll();
        }
        if (usage && usage.usage) {
          const u = usage.usage;
          answer.append(h("div", { class: "usage-line" }, `${usage.model || ""} · ${number((u.prompt_tokens || 0) + (u.completion_tokens || 0))} tokens`));
        }
      } catch (err) {
        if (err.name === "AbortError") body.append(h("span", { class: "muted" }, "\n(stopped)"));
        else { answer.className = "msg error"; answer.querySelector(".who").textContent = "Error"; body.textContent = err.message; }
      } finally {
        controller = null; setRunning(false); scroll(); refreshList();
      }
    };

    send.addEventListener("click", run);
    stop.addEventListener("click", () => controller && controller.abort());
    fresh.addEventListener("click", () => select(newSessionId()));
    input.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); run(); } });
    input.addEventListener("input", () => { input.style.height = "auto"; input.style.height = `${Math.min(input.scrollHeight, 200)}px`; });

    const side = h("div", { class: "stack" }, fresh, list);
    root.append(h("div", { class: "chat" }, side, h("div", { class: "chat-main" }, log, h("div", { class: "composer" }, input, send, stop))));
    ctx.setCount(null);
    await select(sessionId);
    return () => { if (controller) controller.abort(); };
  },
};
