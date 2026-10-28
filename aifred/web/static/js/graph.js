// A small force-directed graph drawn as SVG: no library, deterministic, fine for a few hundred nodes.
import { h } from "./core.js";

const NS = "http://www.w3.org/2000/svg";
const W = 760, H = 460;

function svg(tag, attrs = {}) {
  const el = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, String(v));
  return el;
}

export function layout(nodes, edges, steps = 260) {
  const n = nodes.length;
  nodes.forEach((node, i) => {
    const angle = (2 * Math.PI * i) / Math.max(n, 1);
    node.x = W / 2 + Math.cos(angle) * Math.min(W, H) * 0.35; node.y = H / 2 + Math.sin(angle) * Math.min(W, H) * 0.35;
    node.vx = 0; node.vy = 0;
  });
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const links = edges.map((e) => [byId.get(e.source), byId.get(e.target), e.weight]).filter(([a, b]) => a && b);
  const spread = Math.sqrt((W * H) / Math.max(n, 1)) * 0.8;
  for (let step = 0; step < steps; step++) {
    const cool = 1 - step / steps;
    for (let i = 0; i < n; i++) for (let j = i + 1; j < n; j++) {
      const a = nodes[i], b = nodes[j];
      let dx = a.x - b.x, dy = a.y - b.y, d2 = dx * dx + dy * dy;
      if (d2 < 0.01) { dx = 0.1 * (i - j); dy = 0.1; d2 = dx * dx + dy * dy; }
      const d = Math.sqrt(d2), f = (spread * spread) / d2;
      a.vx += (dx / d) * f; a.vy += (dy / d) * f; b.vx -= (dx / d) * f; b.vy -= (dy / d) * f;
    }
    for (const [a, b, w] of links) {
      const dx = b.x - a.x, dy = b.y - a.y, d = Math.sqrt(dx * dx + dy * dy) || 1, f = ((d - spread * 0.7) * 0.04) * Math.min(w, 3);
      a.vx += (dx / d) * f; a.vy += (dy / d) * f; b.vx -= (dx / d) * f; b.vy -= (dy / d) * f;
    }
    for (const node of nodes) {
      node.vx += (W / 2 - node.x) * 0.01; node.vy += (H / 2 - node.y) * 0.01;
      const speed = Math.hypot(node.vx, node.vy) || 1, cap = 14 * cool + 1;
      node.x += (node.vx / speed) * Math.min(speed, cap); node.y += (node.vy / speed) * Math.min(speed, cap);
      node.x = Math.max(24, Math.min(W - 24, node.x)); node.y = Math.max(24, Math.min(H - 24, node.y));
      node.vx = 0; node.vy = 0;
    }
  }
  return links;
}

export function drawGraph(graph, onPick) {
  const nodes = graph.nodes.map((n) => ({ ...n })), links = layout(nodes, graph.edges);
  const root = svg("svg", { viewBox: `0 0 ${W} ${H}`, class: "graph", role: "img", "aria-label": `Memory graph with ${nodes.length} names` });
  for (const [a, b, w] of links) root.append(svg("line", { x1: a.x, y1: a.y, x2: b.x, y2: b.y, class: "g-edge", "stroke-width": Math.min(1 + w, 5) }));
  const groups = new Map();
  for (const node of nodes) {
    const r = 6 + 3 * Math.sqrt(node.count), kind = node.targets.length > 1 ? "both" : node.targets[0];
    const g = svg("g", { class: `g-node ${kind}`, tabindex: 0, role: "button", "aria-label": `${node.label}, ${node.count} entries` });
    g.append(svg("circle", { cx: node.x, cy: node.y, r }));
    const label = svg("text", { x: node.x, y: node.y + r + 12, "text-anchor": "middle" }); label.textContent = node.label;
    g.append(label);
    const pick = () => { groups.forEach((el) => el.classList.remove("on")); g.classList.add("on"); onPick(node); };
    g.addEventListener("click", pick);
    g.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); } });
    groups.set(node.id, g); root.append(g);
  }
  return root;
}

export function legend() {
  return h("div", { class: "row g-legend muted" },
    h("span", {}, h("i", { class: "dot memory" }), " notes"), h("span", {}, h("i", { class: "dot user" }), " profile"), h("span", {}, h("i", { class: "dot both" }), " both"),
    h("span", {}, "size = entries, line = shared entry"));
}
