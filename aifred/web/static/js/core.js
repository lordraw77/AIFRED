// Shared helpers: DOM construction, the API client, toasts and small formatters.
// Text is always inserted with textContent / text nodes; nothing here ever builds HTML from data.

export const state = { csrf: "", user: "", onUnauthorized: () => {} };

const SVG_NS = "http://www.w3.org/2000/svg";

/** Create an element. attrs: class, text, on<event>, data-*, aria-*, booleans; children: nodes, strings, arrays. */
export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") el.className = value;
    else if (key === "text") el.textContent = value;
    else if (key.startsWith("on") && typeof value === "function") el.addEventListener(key.slice(2), value);
    else if (value === true) el.setAttribute(key, "");
    else el.setAttribute(key, String(value));
  }
  append(el, children);
  return el;
}

function append(el, children) {
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
}

export function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); return el; }

const ICONS = {
  chat: "M3 4h18v12H8l-5 4z",
  sessions: "M4 5h16M4 12h16M4 19h10",
  memory: "M5 4h14v16H5zM9 8h6M9 12h6M9 16h4",
  skills: "M12 3l2.5 5.5L20 9l-4 4 1 6-5-3-5 3 1-6-4-4 5.5-.5z",
  knowledge: "M4 5c0-1.1 3.6-2 8-2s8 .9 8 2-3.6 2-8 2-8-.9-8-2zM4 5v7c0 1.1 3.6 2 8 2s8-.9 8-2V5M4 12v7c0 1.1 3.6 2 8 2s8-.9 8-2v-7",
  library: "M5 3h4v18H5zM11 3h4v18h-4zM17.5 4.5l3.5 1-4 15-3.5-1z",
  schedules: "M12 21a8 8 0 1 0 0-16 8 8 0 0 0 0 16zM12 8v4l3 2M5 3L3 5M19 3l2 2",
  tools: "M14 6a4 4 0 0 0-5 5l-6 6 2 2 6-6a4 4 0 0 0 5-5l-3 3-2-2z",
  mcp: "M8 3v6M16 3v6M5 9h14v4a7 7 0 0 1-14 0zM12 20v2",
  connectors: "M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1",
  models: "M12 3l9 5-9 5-9-5zM3 13l9 5 9-5M3 17l9 5 9-5",
  journey: "M6 4v16M6 6h10l-2 3 2 3H6",
  logs: "M4 5h16M4 10h16M4 15h10M4 20h6",
  config: "M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0M14 4v4M8 10v4M16 16v4",
  keys: "M14 8a4 4 0 1 1-1 2.6L4 20v-3h3v-3h3l1.4-1.4",
  account: "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM4 21c0-4 4-6 8-6s8 2 8 6",
  system: "M4 5h16v10H4zM9 19h6M12 15v4",
  guide: "M5 4h11a3 3 0 0 1 3 3v13H8a3 3 0 0 1-3-3zM5 17a3 3 0 0 1 3-3h11M9 8h6",
  menu: "M4 6h16M4 12h16M4 18h16",
  collapse: "M15 5l-7 7 7 7",
  filter: "M4 5h16l-6 8v6l-4-2v-4z",
  plus: "M12 5v14M5 12h14",
  refresh: "M20 11a8 8 0 1 0-2 6M20 5v6h-6",
  trash: "M5 7h14M9 7V4h6v3M7 7l1 13h8l1-13",
  edit: "M4 20h4L19 9l-4-4L4 16zM14 6l4 4",
  check: "M5 12l5 5 9-10",
  x: "M6 6l12 12M18 6L6 18",
  send: "M4 12l16-8-6 16-2-7z",
  stop: "M7 7h10v10H7z",
  up: "M6 14l6-6 6 6",
  down: "M6 10l6 6 6-6",
  copy: "M9 9h11v11H9zM5 15V4h11",
  warn: "M12 4l10 17H2zM12 10v5M12 18v.5",
};

export function icon(name, size = 16) {
  const svg = document.createElementNS(SVG_NS, "svg");
  for (const [k, v] of Object.entries({ viewBox: "0 0 24 24", width: size, height: size, fill: "none", stroke: "currentColor", "stroke-width": "1.5", "stroke-linecap": "square", "aria-hidden": "true", class: "icon" })) {
    svg.setAttribute(k, v);
  }
  const path = document.createElementNS(SVG_NS, "path");
  path.setAttribute("d", ICONS[name] || ICONS.x);
  svg.append(path);
  return svg;
}

// API ----------------------------------------------------------------------------------------------

export class ApiError extends Error {
  constructor(message, status) { super(message); this.status = status; }
}

function describeDetail(detail, fallback) {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((d) => `${(d.loc || []).filter((p) => p !== "body").join(".")}: ${d.msg}`).join("; ");
  return fallback;
}

export async function api(method, path, body, { signal } = {}) {
  const headers = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET" && state.csrf) headers["X-CSRF-Token"] = state.csrf;
  let response;
  try {
    response = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body), credentials: "same-origin", signal });
  } catch (err) {
    if (err.name === "AbortError") throw err;
    throw new ApiError("Cannot reach the server. Check your connection and retry.", 0);
  }
  if (response.status === 401 && !path.startsWith("/api/auth/login")) {
    state.onUnauthorized();
    throw new ApiError("Your session has ended. Please sign in again.", 401);
  }
  let data = null;
  const text = await response.text();
  if (text) { try { data = JSON.parse(text); } catch (e) { data = null; } }
  if (!response.ok) throw new ApiError(describeDetail(data && data.detail, `Request failed (HTTP ${response.status})`), response.status);
  return data;
}

export const get = (path, opts) => api("GET", path, undefined, opts);
export const post = (path, body = {}) => api("POST", path, body);
export const put = (path, body = {}) => api("PUT", path, body);
export const del = (path) => api("DELETE", path);

// Feedback -----------------------------------------------------------------------------------------

let toastHost;
export function toast(message, kind = "info") {
  if (!toastHost) { toastHost = h("div", { class: "toasts", role: "status", "aria-live": "polite" }); document.body.append(toastHost); }
  const item = h("div", { class: `toast toast-${kind}` }, message);
  toastHost.append(item);
  setTimeout(() => item.remove(), kind === "err" ? 8000 : 4000);
}

/** Run an async action, showing errors as a toast. Returns the result, or undefined on failure. */
export async function attempt(action, success) {
  try {
    const result = await action();
    if (success) toast(success, "ok");
    return result;
  } catch (err) {
    if (err.name !== "AbortError" && err.status !== 401) toast(err.message, "err");
    return undefined;
  }
}

/** A destructive button that asks for an inline confirmation instead of confirm(). */
export function dangerButton(label, onConfirm, { small = true } = {}) {
  const wrap = h("span", { class: "inline-confirm" });
  const reset = () => { clear(wrap); wrap.append(start); };
  const start = h("button", { class: `btn btn-danger${small ? " btn-sm" : ""}`, type: "button", onclick: () => {
    clear(wrap);
    wrap.append(
      h("span", { class: "muted" }, "Sure? "),
      h("button", { class: `btn btn-danger${small ? " btn-sm" : ""}`, type: "button", onclick: onConfirm }, "Yes, " + label.toLowerCase()),
      h("button", { class: `btn${small ? " btn-sm" : ""}`, type: "button", onclick: reset }, "Cancel"),
    );
  } }, label);
  wrap.append(start);
  return wrap;
}

export function button(label, onclick, { primary = false, small = false, iconName = null, title = null, disabled = false } = {}) {
  return h("button", { class: `btn${primary ? " btn-primary" : ""}${small ? " btn-sm" : ""}`, type: "button", onclick, title, disabled }, iconName && icon(iconName), label && h("span", {}, label));
}

/** Disable a button while an async handler runs. */
export function busy(btn, action) {
  return async (...args) => {
    btn.disabled = true;
    try { return await action(...args); } finally { btn.disabled = false; }
  };
}

export function field(label, input, hint) {
  return h("label", { class: "field" }, h("span", { class: "label" }, label), input, hint && h("span", { class: "hint" }, hint));
}

export function panel(title, iconName, ...body) {
  return h("section", { class: "panel" }, title && h("header", { class: "panel-head" }, iconName && icon(iconName), h("h2", {}, title)), h("div", { class: "panel-body" }, body));
}

export function badge(text, kind = "") { return h("span", { class: `tag ${kind}` }, text); }

export function empty(text, action) { return h("div", { class: "empty" }, h("p", {}, text), action); }

/** One row of a history: the head is a button that opens the body in place. `open` is a Set the page keeps, so a redraw keeps the row open. */
export function disclosure(key, open, head, body) {
  const panel = h("div", { class: "disc-body", hidden: !open.has(key) });
  const fill = () => { for (const node of [body()].flat(Infinity)) if (node) panel.append(node); };
  const caret = h("span", { class: "caret", "aria-hidden": "true" }, open.has(key) ? "▾" : "▸");
  const toggle = h("button", { class: "disc-head", type: "button", "aria-expanded": String(open.has(key)) }, caret, head);
  if (open.has(key)) fill();
  toggle.addEventListener("click", () => {
    const now = !open.has(key);
    if (now) open.add(key); else open.delete(key);
    if (now && !panel.childElementCount) fill();
    panel.hidden = !now;
    caret.textContent = now ? "▾" : "▸";
    toggle.setAttribute("aria-expanded", String(now));
  });
  return h("div", { class: "disc" }, toggle, panel);
}

export function skeleton(lines = 3) { return h("div", { class: "skeleton", "aria-hidden": "true" }, Array.from({ length: lines }, () => h("div", { class: "skeleton-line" }))); }

// Formatting ---------------------------------------------------------------------------------------

export function ago(epochSeconds) {
  if (!epochSeconds) return "never";
  const s = Math.max(0, Date.now() / 1000 - epochSeconds);
  if (s < 45) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}

export function duration(seconds) {
  const s = Math.floor(seconds);
  const d = Math.floor(s / 86400), hh = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  return d ? `${d} d ${hh} h` : hh ? `${hh} h ${m} min` : `${m} min`;
}

export function bytes(n) {
  if (n < 1024) return `${n} B`;
  if (n < 1048576) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1073741824) return `${(n / 1048576).toFixed(1)} MB`;
  return `${(n / 1073741824).toFixed(2)} GB`;
}

export const number = (n) => new Intl.NumberFormat("en").format(n);

export function uuid() {
  if (crypto.randomUUID) return crypto.randomUUID();
  return Array.from(crypto.getRandomValues(new Uint8Array(16)), (b) => b.toString(16).padStart(2, "0")).join("");
}

export function store(key, value) {
  try {
    if (value === undefined) return localStorage.getItem(`aifred.${key}`);
    if (value === null) localStorage.removeItem(`aifred.${key}`); else localStorage.setItem(`aifred.${key}`, value);
  } catch (e) { /* storage unavailable: keep working without it */ }
  return null;
}

export function copyText(text) {
  return navigator.clipboard ? navigator.clipboard.writeText(text).then(() => toast("Copied", "ok"), () => toast("Copy failed", "err")) : Promise.resolve(toast("Copy is not available here", "err"));
}
