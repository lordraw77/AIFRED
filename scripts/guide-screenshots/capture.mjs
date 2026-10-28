// Takes the screenshots of the guide against the demo instance (see README.md). Needs Playwright:
//   PLAYWRIGHT_MODULE=/path/to/playwright/index.mjs node scripts/guide-screenshots/capture.mjs <demo log> <output folder>
import fs from "node:fs";

const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || "playwright");
const [logFile, outDir] = process.argv.slice(2);
const log = fs.readFileSync(logFile, "utf8");
const FAKE = log.match(/FAKE (\S+)/)[1];
const MCP = log.match(/MCP (\S+)/)[1];
const BASE = "http://127.0.0.1:18090";
fs.mkdirSync(outDir, { recursive: true });

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 820 }, colorScheme: "dark" });
const problems = [];
page.on("console", (m) => { if (["error", "warning"].includes(m.type()) && !/40[19]|422/.test(m.text())) problems.push(m.text()); });
page.on("response", (r) => { if (r.status() === 404) problems.push(`404 ${r.url()}`); });
page.on("pageerror", (e) => problems.push("pageerror: " + e.message));

const wait = (ms) => page.waitForTimeout(ms);
const shot = async (name) => { await wait(400); await page.screenshot({ path: `${outDir}/${name}.png` }); console.log("shot", name); };
const step = async (name, fn) => { try { await fn(); } catch (e) { console.log("FAIL", name, "-", e.message.split("\n").slice(0, 4).join(" | ")); await page.screenshot({ path: `${outDir}/_fail-${name}.png` }); } };
const api = (method, path, body, raw) => page.evaluate(async ([method, path, body, raw]) => {
  const s = await (await fetch("/api/auth/session")).json();
  const r = await fetch(path, { method, headers: { "Content-Type": raw ? "text/markdown" : "application/json", "X-CSRF-Token": s.csrf_token }, body: raw ? body : body ? JSON.stringify(body) : undefined });
  const text = await r.text();
  try { return JSON.parse(text); } catch (e) { return text; }
}, [method, path, body, raw]);
const go = async (id, query = "") => { await page.evaluate(([id, query]) => { location.hash = `#/${id}${query}`; }, [id, query]); await wait(900); };
const scrollTo = async (locator) => { await locator.scrollIntoViewIfNeeded(); await wait(300); };

// Login ------------------------------------------------------------------------------------
await page.goto(BASE);
await page.waitForSelector("form.login");
await shot("start-1-login");
await page.fill("input[name=username]", "admin"); await page.fill("input[name=password]", "demo-password");
await page.click("button[type=submit]"); await page.waitForSelector(".shell");

// Seed through the API ---------------------------------------------------------------------
const p = await api("POST", "/api/providers", { slug: "demo-llm", type: "local", base_url: FAKE, skip_test: true });
await api("PUT", `/api/providers/${p.id}/models`, [{ model_id: "demo-model-1" }]);
await api("POST", `/api/providers/${p.id}/models/demo-model-1/test`); await api("POST", `/api/providers/${p.id}/models/demo-model-1/test`);
await api("PUT", "/api/routes/default", { entries: [{ provider: "demo-llm", model: "demo-model-1" }] });
await api("PUT", "/api/config", { values: {
  TOOLSETS: "memory, search, skills, web, file, library, agents, cron, mcp:proxmox", TELEGRAM_ALLOWED_IDS: "4242", SCHEDULER_TIMEZONE: "Europe/Rome",
  RAG_ENABLED: true, RAG_EMBED_PROVIDER: "demo-llm", RAG_EMBED_MODEL: "embed-demo", WRITE_APPROVAL: "all",
} });
await api("PUT", "/api/mcp/servers/proxmox", { url: MCP, skip_test: true, instruction: "Proxmox cluster of the homelab. Use it for questions about virtual machines, nodes and backups. Do not use it for the Linux hosts." });
await wait(2500);

// Tools: the test of an instruction consumes the demo model's queued answers, so it runs first
await go("tools");
await step("tools-matrix", async () => { await shot("tools-1-matrix"); });
await step("tools-test", async () => {
  await page.locator(".item-head", { hasText: "mcp:proxmox" }).locator("button").first().click();
  await page.getByRole("button", { name: "Test", exact: true }).click();
  await page.waitForSelector("text=/Score \\d+\\/\\d+/i", { timeout: 40000 });
  const area = page.locator("textarea[aria-label='Instruction for mcp:proxmox']");
  await scrollTo(area);
  await shot("tools-2-test");
});

await api("POST", "/api/memory/memory", { content: "The [[router]] is at 10.70.0.1" });
await api("POST", "/api/memory/memory", { content: "The [[NAS]] backup runs at 02:00 and is kept for 14 days" });
await api("POST", "/api/memory/memory", { content: "[[Proxmox]] cluster: nodes [[pve]] (192.168.0.243) and [[pve02]] (192.168.0.242)" });
await api("POST", "/api/memory/memory", { content: "The [[NAS]] stores the [[Proxmox]] backups" });
await api("POST", "/api/memory/memory", { content: "[[Home Assistant]] runs on [[pve02]]" });
await api("POST", "/api/memory/user", { content: "Name: Alessandro. Prefers answers in Italian" });
await api("POST", "/api/memory/user", { content: "Works with [[Proxmox]], [[Docker]] and [[Home Assistant]]" });
await api("POST", "/api/library", { title: "Fried rice recipe", tags: ["cooking", "dinner"], source: "chat", prompt: "Give me the recipe for fried rice", content: "# Fried rice\n\n- 300 g cooked rice, a day old\n- 2 eggs\n- 2 spring onions\n- soy sauce\n\nFry the eggs, add the rice and the onions, finish with soy sauce." });
await api("POST", "/api/library", { title: "NAS restore checklist", tags: ["homelab"], source: "agent", prompt: "Write the checklist to restore the NAS backup", content: "1. Stop the backup job\n2. Mount the backup volume\n3. Restore to a temporary folder\n4. Compare checksums\n5. Move the files back" });
await api("POST", "/api/library", { title: "Greeting", tags: [], source: "manual", prompt: "Say hello", content: "Hi there." });
await api("PUT", "/api/skills/backup-guide", { description: "How the nightly backup of the NAS works and how to restore a file", body: "# Backup guide\n\nThe nightly backup runs at **02:00** and is kept for 14 days.\n\n## Restore a file\n\n1. Open the Proxmox Backup Server\n2. Pick the day\n3. Download the file\n" });
await api("PUT", "/api/skills/server-report", { description: "Write the weekly report of the servers: disks, updates, failed services", body: "# Server report\n\nCollect disk usage, pending updates and failed services, then write five bullet points." });
await api("POST", "/api/rag/collections", { name: "homelab", description: "Runbooks and notes of the homelab" });
await api("POST", "/api/rag/documents?collection=homelab&name=proxmox-upgrade.md", "# Proxmox upgrade\n\n## Backup first\n\nTake a snapshot of every VM before an upgrade.\n\n## Troubleshooting\n\nIf the cluster loses quorum check corosync with pvecm status.\n", true);
await api("POST", "/api/rag/documents?collection=homelab&name=network-map.md", "# Network map\n\nVLAN 10 carries the servers, VLAN 20 the cameras and VLAN 30 the guests. The resolver runs on 10.70.0.2.\n", true);
await api("POST", "/api/rag/documents?collection=homelab&name=backup.md", "# Backup\n\nThe nightly backup of the NAS runs at 02:00 and is kept for 14 days. Restore from the Proxmox Backup Server.\n", true);
await wait(5000);
await api("POST", "/api/rag/golden", { question: "How do I check the quorum of the cluster?", source: "proxmox-upgrade.md", phrase: "pvecm status" });
await api("POST", "/api/rag/golden", { question: "Which VLAN carries the cameras?", source: "network-map.md", phrase: "VLAN 20" });
await api("POST", "/api/rag/golden", { question: "How long are the backups kept?", source: "backup.md", phrase: "14 days" });
await api("POST", "/api/rag/evaluate", { k: 5, label: "first run" });
await api("PUT", "/api/schedules/morning-news", { expression: "ogni mattina alle 6", prompt: "Search the web for today's news about Python and write five short bullet points.", deliver_chat: 4242 });
await api("PUT", "/api/schedules/weekly-report", { expression: "ogni lunedì alle 9", prompt: "Write the weekly report of the servers using the server-report skill.", deliver_chat: 4242 });
await api("POST", "/api/schedules/morning-news/run"); await wait(3000);
await api("PUT", "/api/agents/researcher", { enabled: true }); await api("PUT", "/api/agents/sysadmin", { enabled: true });
await api("POST", "/api/agents/researcher/run", { task: "Find the latest news about Python" }); await wait(2500);
await api("PUT", "/api/instructions/mcp:proxmox", { text: "Proxmox cluster of the homelab. Use it for questions about virtual machines, nodes and backups. Do not use it for the Linux hosts.", enabled: true, score: "5/6" });

// Chat -------------------------------------------------------------------------------------
await step("chat", async () => {
  await go("chat", "?s=web%3Avms");
  await page.waitForSelector(".msg.assistant");
  await shot("chat-1-conversation");
  await page.locator(".chip").first().click();
  await shot("chat-2-tool-result");
  await go("chat", "?s=web%3Aroma");
  await page.waitForSelector(".msg.assistant");
  await page.locator(".chip").first().click();
  await shot("chat-3-agent-answer");
});
await step("commands", async () => {
  await go("chat");
  await page.getByRole("button", { name: "New" }).click(); await wait(500);
  await page.fill("textarea[aria-label=Message]", "/help");
  await page.getByRole("button", { name: "Send" }).click(); await wait(1500);
  await shot("commands-1-help");
});

await step("library", async () => {
  await go("chat");
  await page.getByRole("button", { name: "New" }).click(); await wait(500);
  await page.fill("textarea[aria-label=Message]", "Give me the recipe for pancakes");
  await page.getByRole("button", { name: "Send" }).click(); await wait(2000);
  await page.getByRole("button", { name: "Save to library" }).last().click(); await wait(500);
  await shot("library-1-save");
  await go("library"); await wait(800);
  await page.locator(".lib-head", { hasText: "Fried rice recipe" }).click(); await wait(800);
  await shot("library-2-list");
  await page.locator(".lib-head", { hasText: "Greeting" }).click(); await wait(500);
  await page.getByRole("button", { name: "Regenerate" }).click(); await wait(2500);
  await scrollTo(page.locator(".draft").first());
  await shot("library-3-regenerate");
});

// Sessions, memory, skills -----------------------------------------------------------------
await step("sessions", async () => {
  await go("sessions"); await shot("sessions-1-overview");
  await page.getByRole("tab", { name: "history" }).click(); await wait(500);
  await page.fill("input[aria-label=Search]", "Dybala"); await page.keyboard.press("Enter"); await wait(1200);
  await shot("sessions-2-history");
});
await step("memory", async () => { await go("memory"); await shot("memory-1"); await page.evaluate(() => document.querySelector(".graph")?.scrollIntoView()); await shot("memory-2"); });
await step("skills", async () => {
  await go("skills");
  await page.locator(".list-item", { hasText: "backup-guide" }).click(); await wait(600);
  await page.getByRole("tab", { name: "preview" }).click(); await wait(500);
  await shot("skills-1-editor");
});

// Knowledge --------------------------------------------------------------------------------
await step("knowledge", async () => {
  await go("knowledge"); await wait(1200);
  await shot("knowledge-1-overview");
  await page.fill("input[placeholder='Test a search']", "backup restore"); await page.keyboard.press("Enter"); await wait(1500);
  await scrollTo(page.locator("text=Test search").first());
  await shot("knowledge-2-search");
  await scrollTo(page.locator("text=Retrieval quality").first());
  await page.evaluate(() => { document.getElementById("content").scrollTop = document.getElementById("content").scrollHeight; }); await wait(400);
  await shot("knowledge-3-quality");
});

// Schedules and agents ---------------------------------------------------------------------
await step("schedules", async () => {
  await go("schedules"); await wait(800);
  await shot("schedules-1-list");
  await page.getByRole("button", { name: "New task" }).click(); await wait(500);
  await page.locator("input[placeholder*='every morning']").fill("dal lunedì al venerdì alle 8:30");
  await wait(1200);
  await shot("schedules-2-sentence");
  await page.locator("input[placeholder*='every morning']").fill("every monday and thursday at 9am"); await wait(1200);
  await shot("schedules-3-english");
});
await step("agents", async () => {
  await go("agents"); await wait(800); await shot("agents-1-list");
  await page.getByRole("button", { name: "New agent" }).click(); await wait(500);
  await shot("agents-2-form");
});

// MCP, models ------------------------------------------------------------------------------
await step("mcp", async () => {
  await go("mcp"); await wait(1200); await shot("mcp-1-servers");
  await page.getByRole("button", { name: "Add server" }).click(); await wait(500);
  await shot("mcp-2-form");
});
await step("models", async () => {
  await go("models"); await wait(1200);
  await page.getByRole("button", { name: /^Models \(/ }).first().click(); await wait(500);
  await shot("models-1-providers");
  await scrollTo(page.locator("text=Routes").first());
  await page.evaluate(() => { const c = document.getElementById("content"); c.scrollTop = c.scrollHeight; }); await wait(400);
  await shot("models-2-routes-health");
});

// Journey, logs, config, admin -------------------------------------------------------------
await step("journey", async () => { await go("journey"); await shot("journey-1"); });
await step("logs", async () => { await go("logs"); await wait(1500); await shot("logs-1"); });
await step("config", async () => {
  await go("config"); await page.fill("input[aria-label='Filter settings']", "scheduler"); await wait(600); await shot("config-1-filter");
});
await step("keys", async () => {
  await go("keys");
  await page.fill("input[placeholder*='Open WebUI']", "Open WebUI");
  await page.getByRole("button", { name: "Create key" }).click(); await wait(900);
  await shot("keys-1");
});
await step("account", async () => { await go("account"); await shot("account-1"); });
await step("system", async () => { await go("system"); await shot("system-1"); });
await step("guide", async () => { await go("guide", "?s=start&l=en"); await wait(1500); await shot("guide-1"); });

console.log("console problems:", problems.length ? problems.join(" || ") : "none");
await browser.close();
