// Entry point: checks the session, shows the login page or the application shell, and routes between pages.
import { ApiError, attempt, button, clear, field, get, h, icon, post, state, store, toast, ago } from "./core.js";

const PAGES = [
  { id: "chat", label: "Chat", icon: "chat", key: "c", load: () => import("./pages/chat.js") },
  { id: "sessions", label: "Sessions", icon: "sessions", key: "s", load: () => import("./pages/sessions.js") },
  { id: "memory", label: "Memory", icon: "memory", key: "m", load: () => import("./pages/memory.js") },
  { id: "skills", label: "Skills", icon: "skills", key: "k", load: () => import("./pages/skills.js") },
  { id: "knowledge", label: "Knowledge", icon: "knowledge", load: () => import("./pages/knowledge.js") },
  { id: "schedules", label: "Schedules", icon: "schedules", load: () => import("./pages/schedules.js") },
  { id: "agents", label: "Agents", icon: "models", load: () => import("./pages/agents.js") },
  { id: "tools", label: "Tools", icon: "tools", load: () => import("./pages/tools.js") },
  { id: "mcp", label: "MCP", icon: "mcp", load: () => import("./pages/mcp.js") },
  { id: "models", label: "Models", icon: "models", load: () => import("./pages/models.js") },
  { id: "journey", label: "Journey", icon: "journey", key: "j", load: () => import("./pages/journey.js") },
  { id: "logs", label: "Logs", icon: "logs", load: () => import("./pages/logs.js") },
  { id: "config", label: "Config", icon: "config", load: () => import("./pages/config.js") },
  { id: "keys", label: "API keys", icon: "keys", load: () => import("./pages/admin.js").then((m) => m.keysPage) },
  { id: "account", label: "Account", icon: "account", load: () => import("./pages/admin.js").then((m) => m.accountPage) },
  { id: "system", label: "System", icon: "system", load: () => import("./pages/admin.js").then((m) => m.systemPage) },
  { id: "guide", label: "Guide", icon: "guide", load: () => import("./pages/guide.js") },
];

const root = document.getElementById("app");
let cleanup = null;
let statusTimer = null;
let navToken = 0;

state.onUnauthorized = () => { state.csrf = ""; showLogin(); };

// Theme ------------------------------------------------------------------------------------------

const THEMES = ["auto", "teal", "sand"];
function currentTheme() { const t = store("theme"); return THEMES.includes(t) ? t : "auto"; }
function applyTheme(theme) {
  if (theme === "auto") delete document.documentElement.dataset.theme; else document.documentElement.dataset.theme = theme;
  store("theme", theme === "auto" ? null : theme);
}

// Login ------------------------------------------------------------------------------------------

function showLogin(message = "") {
  stopShell();
  clear(root);
  const user = h("input", { type: "text", name: "username", autocomplete: "username", required: true, autofocus: true });
  const pass = h("input", { type: "password", name: "password", autocomplete: "current-password", required: true });
  const error = h("div", { class: "notice err", role: "alert", hidden: !message }, message);
  const submit = h("button", { class: "btn btn-primary", type: "submit" }, "Sign in");
  const form = h("form", { class: "login", onsubmit: async (event) => {
    event.preventDefault();
    submit.disabled = true;
    error.hidden = true;
    try {
      const info = await post("/api/auth/login", { username: user.value, password: pass.value });
      state.csrf = info.csrf_token; state.user = info.username;
      await start();
    } catch (err) {
      error.textContent = err.status === 429 ? `${err.message}` : err.status === 401 ? "Wrong username or password." : err.message;
      error.hidden = false;
      pass.value = "";
    } finally { submit.disabled = false; }
  } },
    h("img", { src: "/static/brand/mark.svg", alt: "" }),
    h("h1", {}, "AIFRED"),
    field("Username", user), field("Password", pass), error, submit,
  );
  root.append(h("div", { class: "login-wrap" }, form));
  document.title = "Sign in · AIFRED";
}

// Keyboard: g then c/s/m/k/j jumps to a page, / focuses the search box, Esc closes the drawer ----------

let goPending = 0;
function onKey(e) {
  if (e.key === "Escape") { window.__shell && window.__shell.classList.remove("drawer"); return; }
  if (e.ctrlKey || e.metaKey || e.altKey || ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName)) return;
  if (e.key === "/" && window.__focusSearch) { e.preventDefault(); window.__focusSearch(); return; }
  if (goPending && Date.now() - goPending < 1500) {
    const page = PAGES.find((p) => p.key === e.key);
    goPending = 0;
    if (page) { e.preventDefault(); location.hash = `#/${page.id}`; }
    return;
  }
  goPending = e.key === "g" ? Date.now() : 0;
}

// Shell ------------------------------------------------------------------------------------------

function stopShell() {
  if (cleanup) { try { cleanup(); } catch (e) { /* page cleanup must not block */ } cleanup = null; }
  if (statusTimer) { clearInterval(statusTimer); statusTimer = null; }
  window.removeEventListener("hashchange", route);
  document.removeEventListener("keydown", onKey);
  window.__shell = null;
}

function showShell() {
  stopShell();
  clear(root);
  const shell = h("div", { class: `shell${store("collapsed") === "1" ? " collapsed" : ""}` });
  const nav = h("nav", { class: "nav", "aria-label": "Sections" }, PAGES.map((p) =>
    h("a", { class: "nav-item", href: `#/${p.id}`, "data-page": p.id, onclick: () => shell.classList.remove("drawer") }, icon(p.icon), h("span", {}, p.label))));
  const gateway = h("span", { class: "dot off" }, "starting");
  const sessions = h("span", {}, "Sessions: –");
  const themeBtn = h("button", { class: "btn btn-sm", type: "button", title: "Change theme", onclick: () => {
    const next = THEMES[(THEMES.indexOf(currentTheme()) + 1) % THEMES.length];
    applyTheme(next); themeBtn.textContent = `Theme: ${next}`;
  } }, `Theme: ${currentTheme()}`);
  const collapse = h("button", { class: "btn btn-sm collapse-btn", type: "button", title: "Collapse the sidebar", "aria-label": "Collapse the sidebar", onclick: () => {
    shell.classList.toggle("collapsed"); store("collapsed", shell.classList.contains("collapsed") ? "1" : "0");
  } }, icon("collapse"));
  const restart = button("Restart", async () => {
    if (await attempt(() => post("/api/restart"), "Telegram and MCP restarted")) refresh();
  }, { small: true, iconName: "refresh", title: "Restart the Telegram poller and reconnect the MCP servers" });
  const logout = button("Sign out", async () => { await attempt(() => post("/api/auth/logout")); state.csrf = ""; showLogin(); }, { small: true });

  const sidebar = h("aside", { class: "sidebar" },
    h("div", { class: "brand" }, h("img", { src: "/static/brand/mark.svg", alt: "" }), h("span", { class: "name" }, "AIFRED"), collapse),
    nav,
    h("div", { class: "side-foot" }, h("div", { class: "full" }, h("div", {}, "Telegram: ", gateway), sessions), h("div", { class: "full row" }, restart, themeBtn, logout)),
  );
  const title = h("h1", {});
  const menu = h("button", { class: "btn btn-sm menu-btn", type: "button", "aria-label": "Open the menu", onclick: () => shell.classList.add("drawer") }, icon("menu"));
  const banner = h("div", { class: "banner", role: "alert", hidden: true });
  const content = h("div", { class: "content", id: "content" });
  const help = h("a", { class: "btn btn-sm help-link", href: "#/guide", title: "Open the guide for this page" }, icon("guide"), h("span", {}, "Guide"));
  const main = h("main", { class: "main" }, h("header", { class: "topbar" }, menu, title, h("span", { class: "spacer" }), help), banner, content);
  shell.append(sidebar, h("div", { class: "scrim", onclick: () => shell.classList.remove("drawer") }), main);
  root.append(shell);
  shell.ctx = { title, content, banner, nav, help };

  const refresh = async () => {
    try {
      const s = await get("/api/status");
      const t = s.telegram;
      gateway.className = `dot ${t.state === "running" ? "ok" : t.state === "error" ? "err" : t.state === "starting" ? "warn" : "off"}`;
      gateway.textContent = t.state === "running" ? `@${t.bot}` : t.state;
      sessions.textContent = `Sessions: ${s.sessions}`;
    } catch (err) { /* the banner is shown by the page that fails; the status block just keeps its last value */ }
  };
  refresh();
  statusTimer = setInterval(refresh, 15000);
  document.addEventListener("keydown", onKey);
  window.addEventListener("hashchange", route);
  window.__shell = shell;
}

async function route() {
  const shell = window.__shell;
  if (!shell) return;
  const id = (location.hash.match(/^#\/([a-z]+)/) || [])[1] || "chat";
  const page = PAGES.find((p) => p.id === id) || PAGES[0];
  const token = ++navToken;
  const { title, content, banner, nav, help } = shell.ctx;
  if (cleanup) { try { cleanup(); } catch (e) { /* ignore */ } cleanup = null; }
  for (const link of nav.children) {
    if (link.dataset.page === page.id) link.setAttribute("aria-current", "page"); else link.removeAttribute("aria-current");
  }
  title.textContent = page.label;
  help.hidden = page.id === "guide";
  help.href = `#/guide?s=${page.id}`;
  document.title = `${page.label} · AIFRED`;
  banner.hidden = true;
  clear(content);
  try {
    const mod = await page.load();
    const impl = mod.default || mod;
    if (token !== navToken) return;
    const result = await impl.render(content, { setCount: (n) => { title.textContent = page.label; if (n !== null && n !== undefined) title.append(" ", h("span", { class: "count" }, String(n))); }, banner: (msg) => {
      clear(banner); if (msg) banner.append(icon("warn"), h("span", {}, msg), button("Retry", route, { small: true })); banner.hidden = !msg;
    } });
    if (token === navToken) cleanup = typeof result === "function" ? result : null; else if (typeof result === "function") result();
  } catch (err) {
    if (token !== navToken || err.status === 401) return;
    banner.hidden = false; clear(banner);
    banner.append(icon("warn"), h("span", {}, err instanceof ApiError ? err.message : `The page failed to load: ${err.message}`), button("Retry", route, { small: true }));
  }
}

async function start() {
  if (!location.hash) {  // first run: a fresh install has no model yet, so start where it is set up
    const routes = await get("/api/routes").catch(() => []);
    const main = routes.find((r) => r.name === "default");
    if (!main || !main.entries.length) location.hash = "#/models";
  }
  showShell();
  await route();
}

async function boot() {
  try {
    const info = await get("/api/auth/session");
    state.csrf = info.csrf_token; state.user = info.username;
    await start();
  } catch (err) {
    showLogin(err.status && err.status !== 401 ? err.message : "");
  }
}

boot();
