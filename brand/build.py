"""Generate the AIFRED brand assets (SVG, PNG, ICO, manifest, tokens, guide).

Run from the repository root:  python brand/build.py
Requires Pillow and Playwright (Chromium) only for the raster outputs.
"""

from __future__ import annotations

import base64
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT_PNG = ROOT / "png"
STATIC = ROOT.parent / "aifred" / "web" / "static"

# --------------------------------------------------------------------------- palette
TEAL = {"bg": "#04191a", "surface": "#07282a", "line": "#14504f", "text": "#f3e6d0",
        "accent": "#ffe6cb", "ok": "#4ade80"}
SAND = {"bg": "#f6f0e4", "surface": "#fffaf0", "line": "#d6c9b0", "text": "#1c2a28",
        "accent": "#0e5c57", "ok": "#1f8a4c"}


# --------------------------------------------------------------------------- geometry
def _fmt(v: float) -> str:
    return f"{v:.2f}".rstrip("0").rstrip(".")


def mark_shapes(fill: str, stem: str, scale: float = 0.95, cx: float = 32, cy: float = 32) -> str:
    """The AIFRED mark: an angular A whose crossbar is a vertical stem (the "I")."""
    base = [(0, 48), (15, 0), (21, 0), (36, 48), (29, 48), (18, 12.8), (7, 48)]
    ox, oy = cx - 18 * scale, cy - 24 * scale
    pts = " ".join(f"{_fmt(ox + x * scale)},{_fmt(oy + y * scale)}" for x, y in base)
    sx, sy = ox + 14.5 * scale, oy + 26 * scale
    return (f'<polygon points="{pts}" fill="{fill}"/>'
            f'<rect x="{_fmt(sx)}" y="{_fmt(sy)}" width="{_fmt(7 * scale)}" '
            f'height="{_fmt(22 * scale)}" fill="{stem}"/>')


def tile(p: dict[str, str], *, radius: float = 12, border: bool = True, scale: float = 0.95) -> str:
    stroke = f' stroke="{p["line"]}" stroke-width="1"' if border else ""
    inset = 0.5 if border else 0
    return (f'<rect x="{inset}" y="{inset}" width="{64 - 2 * inset}" height="{64 - 2 * inset}" '
            f'rx="{radius}" fill="{p["bg"]}"{stroke}/>'
            + mark_shapes(p["accent"], p["ok"], scale))


def svg(view: str, body: str, title: str, extra: str = "") -> str:
    w, h = view.split()[2:]
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{view}" width="{w}" height="{h}" '
            f'role="img"{extra}><title>{title}</title>{body}</svg>\n')


def wordmark_shapes(fill: str, i_fill: str) -> str:
    """AIFRED set in constructed geometric capitals, 232 x 48 units, stroke 7."""
    f, g = fill, i_fill
    a = '<polygon points="0,48 15,0 21,0 36,48 29,48 18,12.8 7,48" fill="%s"/>' % f
    i = f'<rect x="48" y="0" width="7" height="48" fill="{g}"/>'
    ff = (f'<rect x="67" y="0" width="7" height="48" fill="{f}"/>'
          f'<rect x="67" y="0" width="30" height="7" fill="{f}"/>'
          f'<rect x="67" y="20.5" width="24" height="7" fill="{f}"/>')
    r = (f'<rect x="109" y="0" width="7" height="48" fill="{f}"/>'
         f'<path d="M112.5 3.5H126a10.5 10.5 0 0 1 0 21H112.5" fill="none" stroke="{f}" stroke-width="7"/>'
         f'<polygon points="120,28 128,28 140,48 132,48" fill="{f}"/>')
    e = (f'<rect x="152" y="0" width="7" height="48" fill="{f}"/>'
         f'<rect x="152" y="0" width="30" height="7" fill="{f}"/>'
         f'<rect x="152" y="20.5" width="25" height="7" fill="{f}"/>'
         f'<rect x="152" y="41" width="30" height="7" fill="{f}"/>')
    d = (f'<path d="M197.5 3.5H208A20.5 20.5 0 0 1 208 44.5H197.5Z" fill="none" stroke="{f}" '
         f'stroke-width="7" stroke-linejoin="miter"/>'
         f'<rect x="194" y="0" width="7" height="48" fill="{f}"/>')
    return a + i + ff + r + e + d


def lockup_body(p: dict[str, str]) -> str:
    s = 0.58
    return (f'<g transform="translate(0 0) scale(0.875)">{tile(p)}</g>'
            f'<g transform="translate(74 14.1) scale({s})">{wordmark_shapes(p["accent"], p["ok"])}</g>')


# --------------------------------------------------------------------------- vector files
def write_svgs() -> dict[str, str]:
    files: dict[str, str] = {}
    files["mark.svg"] = svg("0 0 64 64", mark_shapes(TEAL["accent"], TEAL["ok"], 1.0),
                            "AIFRED mark (for dark backgrounds)")
    files["mark-light.svg"] = svg("0 0 64 64", mark_shapes(SAND["accent"], SAND["ok"], 1.0),
                                  "AIFRED mark (for light backgrounds)")
    files["mark-mono.svg"] = svg("0 0 64 64", mark_shapes("currentColor", "currentColor", 1.0),
                                 "AIFRED mark (single colour)")
    files["icon.svg"] = svg("0 0 64 64", tile(TEAL), "AIFRED app icon")
    files["icon-light.svg"] = svg("0 0 64 64", tile(SAND), "AIFRED app icon (light)")
    files["favicon.svg"] = svg("0 0 64 64", tile(TEAL, radius=10, border=False, scale=1.1), "AIFRED")
    files["wordmark.svg"] = svg("0 0 232 48", wordmark_shapes(TEAL["accent"], TEAL["ok"]),
                                "AIFRED wordmark (for dark backgrounds)")
    files["wordmark-light.svg"] = svg("0 0 232 48", wordmark_shapes(SAND["accent"], SAND["ok"]),
                                      "AIFRED wordmark (for light backgrounds)")
    files["wordmark-mono.svg"] = svg("0 0 232 48", wordmark_shapes("currentColor", "currentColor"),
                                     "AIFRED wordmark (single colour)")
    files["lockup.svg"] = svg("0 0 209 56", lockup_body(TEAL), "AIFRED")
    files["lockup-light.svg"] = svg("0 0 209 56", lockup_body(SAND), "AIFRED")
    for name, content in files.items():
        (ROOT / name).write_text(content, encoding="utf-8")
    return files


# --------------------------------------------------------------------------- raster files
def _data_uri(svg_text: str) -> str:
    return "data:image/svg+xml;base64," + base64.b64encode(svg_text.encode()).decode()


def write_rasters(files: dict[str, str]) -> None:
    from playwright.sync_api import sync_playwright

    OUT_PNG.mkdir(exist_ok=True)
    maskable = svg("0 0 64 64",
                   f'<rect width="64" height="64" fill="{TEAL["bg"]}"/>'
                   + mark_shapes(TEAL["accent"], TEAL["ok"], 0.62), "AIFRED maskable")
    solid = svg("0 0 64 64",
                f'<rect width="64" height="64" fill="{TEAL["bg"]}"/>'
                + mark_shapes(TEAL["accent"], TEAL["ok"], 0.8), "AIFRED")
    jobs = [(f"icon-{n}.png", files["icon.svg"], n) for n in (16, 32, 48, 64, 128, 192, 256, 512)]
    jobs += [("favicon-16.png", files["favicon.svg"], 16), ("favicon-32.png", files["favicon.svg"], 32),
             ("favicon-48.png", files["favicon.svg"], 48),
             ("apple-touch-icon.png", solid, 180), ("maskable-512.png", maskable, 512),
             ("mark-512.png", files["mark.svg"], 512)]
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for name, source, size in jobs:
            page = browser.new_page(viewport={"width": size, "height": size})
            page.set_content(f'<body style="margin:0;background:transparent">'
                             f'<img src="{_data_uri(source)}" width="{size}" height="{size}" '
                             f'style="display:block"></body>')
            page.screenshot(path=str(OUT_PNG / name), omit_background=True)
            page.close()
        og_html = f"""<body style="margin:0;width:1200px;height:630px;background:{TEAL['bg']};
            position:relative;overflow:hidden;font-family:'DejaVu Sans Mono',monospace">
          <div style="position:absolute;inset:36px;border:1px solid {TEAL['line']}"></div>
          <img src="{_data_uri(files['mark.svg'])}" width="150" height="150"
               style="position:absolute;left:96px;top:140px">
          <img src="{_data_uri(files['wordmark.svg'])}" width="696" height="144"
               style="position:absolute;left:96px;top:330px">
          <div style="position:absolute;left:96px;top:508px;color:{TEAL['text']};font-size:26px;
               letter-spacing:.14em;text-transform:uppercase;opacity:.85">
               Self-hosted personal agent</div>
          <div style="position:absolute;right:96px;top:508px;color:{TEAL['ok']};font-size:22px;
               letter-spacing:.12em">&#9679; ONLINE</div></body>"""
        page = browser.new_page(viewport={"width": 1200, "height": 630})
        page.set_content(og_html)
        page.screenshot(path=str(OUT_PNG / "og-image.png"))
        page.close()
        browser.close()

    from PIL import Image
    imgs = [Image.open(OUT_PNG / f"favicon-{n}.png").convert("RGBA") for n in (48, 32, 16)]
    imgs[0].save(ROOT / "favicon.ico", format="ICO", sizes=[(48, 48), (32, 32), (16, 16)],
                 append_images=imgs[1:])


# --------------------------------------------------------------------------- text files
def write_text_files() -> None:
    manifest = {
        "name": "AIFRED", "short_name": "AIFRED", "description": "Self-hosted personal agent",
        "start_url": "/", "display": "standalone",
        "background_color": TEAL["bg"], "theme_color": TEAL["bg"],
        "icons": [
            {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png"},
            {"src": "/maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
            {"src": "/favicon.svg", "sizes": "any", "type": "image/svg+xml"},
        ],
    }
    (ROOT / "manifest.webmanifest").write_text(json.dumps(manifest, indent=2) + "\n")

    def block(sel: str, p: dict[str, str]) -> str:
        rows = "".join(f"  --{k.replace('_', '-')}: {v};\n" for k, v in p.items())
        return f"{sel} {{\n{rows}}}\n"

    css = ("/* AIFRED design tokens: Teal (dark, default) and Sand (light). */\n"
           + block(":root", TEAL)
           + "\n@media (prefers-color-scheme: light) {\n  :root:not([data-theme=\"teal\"]) {\n"
           + "".join(f"    --{k}: {v};\n" for k, v in SAND.items()) + "  }\n}\n\n"
           + block(':root[data-theme="sand"]', SAND) + "\n" + block(':root[data-theme="teal"]', TEAL)
           + "\n:root {\n  --font-display: \"Space Grotesk\", \"Helvetica Neue\", Arial, sans-serif;\n"
             "  --font-mono: ui-monospace, \"JetBrains Mono\", \"DejaVu Sans Mono\", monospace;\n"
             "  --font-text: \"Source Serif 4\", Georgia, serif;\n}\n")
    (ROOT / "tokens.css").write_text(css)


def write_guide(files: dict[str, str]) -> None:
    def sw(name: str, hexv: str, dark_text: bool) -> str:
        r, g, b = (int(hexv[i:i + 2], 16) for i in (1, 3, 5))
        dark_text = (0.299 * r + 0.587 * g + 0.114 * b) > 140
        fg = "#04191a" if dark_text else "#f3e6d0"
        return (f'<div class="sw" style="background:{hexv};color:{fg}"><b>{name}</b>'
                f'<span>{hexv}</span></div>')

    teal = "".join(sw(k, v, k in ("text", "accent", "ok")) for k, v in TEAL.items())
    sand = "".join(sw(k, v, k not in ("accent",)) for k, v in SAND.items())
    inline = {k: v.replace("<title>", "<title>").strip() for k, v in files.items()}
    sizes = "".join(f'<figure><img src="png/icon-{n}.png" width="{min(n, 128)}" height="{min(n, 128)}">'
                    f'<figcaption>{n} px</figcaption></figure>' for n in (16, 32, 48, 64, 128, 192))
    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AIFRED brand guide</title>
<link rel="icon" href="favicon.svg" type="image/svg+xml">
<style>
:root{{--bg:#04191a;--surface:#07282a;--line:#14504f;--text:#f3e6d0;--muted:#93b0a8;--accent:#ffe6cb;--ok:#4ade80}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);
font:16px/1.55 Georgia,serif}}main{{max-width:1040px;margin:0 auto;padding:40px 16px 80px}}
h1,h2{{font-family:ui-monospace,"DejaVu Sans Mono",monospace;text-transform:uppercase;letter-spacing:.1em;
font-weight:600}}h1{{font-size:22px;margin:0 0 4px}}h2{{font-size:14px;color:var(--muted);margin:48px 0 16px;
border-top:1px solid var(--line);padding-top:16px}}p{{max-width:68ch;margin:0 0 12px}}
.mono{{font-family:ui-monospace,"DejaVu Sans Mono",monospace;font-size:13px;color:var(--muted)}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px}}
.card{{border:1px solid var(--line);background:var(--surface);padding:24px;display:flex;flex-direction:column;
gap:12px;align-items:center;justify-content:center;min-height:180px}}
.card.light{{background:#f6f0e4;border-color:#d6c9b0;color:#1c2a28}}
.card small{{font-family:ui-monospace,monospace;font-size:11px;letter-spacing:.08em;text-transform:uppercase;
opacity:.7}}.card svg{{max-width:100%;height:auto}}
.sw{{padding:14px;display:flex;justify-content:space-between;font-family:ui-monospace,monospace;font-size:12px;
border:1px solid rgba(0,0,0,.15)}}.sws{{display:grid;gap:0;margin-bottom:16px}}
figure{{margin:0;text-align:center}}figcaption{{font:11px ui-monospace,monospace;color:var(--muted);margin-top:6px}}
.row{{display:flex;gap:24px;align-items:flex-end;flex-wrap:wrap}}
pre{{background:var(--surface);border:1px solid var(--line);padding:16px;overflow:auto;font-size:13px;
color:var(--accent)}}ul{{padding-left:20px}}li{{margin-bottom:4px}}
</style></head><body><main>
<h1>AIFRED</h1><p class="mono">Brand guide v0.1 · self-hosted personal agent</p>
<h2>Idea</h2>
<p>The mark is a sharp-edged <b>A</b> whose crossbar is replaced by a vertical stem: the letter
<b>I</b> inside the <b>A</b>, read as "AI". The stem is the only coloured element (status green) and echoes the
"online" indicator of the dashboard. Straight lines, no shadows, a thin 1 px border like the web console.</p>
<h2>Mark</h2><div class="grid">
<div class="card">{inline['mark.svg'].replace('width="64" height="64"','width="120" height="120"')}<small>On dark</small></div>
<div class="card light">{inline['mark-light.svg'].replace('width="64" height="64"','width="120" height="120"')}<small>On light</small></div>
<div class="card" style="color:#f3e6d0">{inline['mark-mono.svg'].replace('width="64" height="64"','width="120" height="120"')}<small>Monochrome</small></div>
<div class="card">{inline['icon.svg'].replace('width="64" height="64"','width="120" height="120"')}<small>App icon</small></div>
</div>
<h2>Wordmark and lockup</h2><div class="grid">
<div class="card">{inline['wordmark.svg'].replace('width="232" height="48"','width="232" height="48"')}<small>Wordmark</small></div>
<div class="card light">{inline['wordmark-light.svg']}<small>Wordmark on light</small></div>
<div class="card">{inline['lockup.svg'].replace('width="209" height="56"','width="260" height="70"')}<small>Horizontal lockup</small></div>
<div class="card light">{inline['lockup-light.svg'].replace('width="209" height="56"','width="260" height="70"')}<small>Lockup on light</small></div>
</div>
<p class="mono" style="margin-top:12px">The wordmark is constructed from rectangles and arcs (no font required);
the A is a bar-less Λ so "AI" reads as in the mark.</p>
<h2>Clear space and minimum sizes</h2>
<ul><li>Clear space: half the height of the A on every side.</li>
<li>Mark: 16 px minimum; wordmark: 72 px wide minimum; lockup: 120 px minimum.</li>
<li>Use the green stem only on the dark Teal or light Sand backgrounds; use the monochrome mark elsewhere.</li>
<li>Do not rotate, skew, add shadows or gradients, or change the proportions.</li></ul>
<h2>Colour</h2><div class="sws">{teal}</div><div class="sws">{sand}</div>
<p class="mono">Teal is the default theme, Sand the light alternative. Tokens are in <code>tokens.css</code>.</p>
<h2>Typography</h2>
<p><b>Titles</b>: wide uppercase sans, +0.06 em tracking (Space Grotesk, falling back to Helvetica/Arial).
<b>Labels and values</b>: uppercase monospace, +0.08 em. <b>Text</b>: readable serif, 15 px.</p>
<h2>Favicon and icons</h2><div class="row">{sizes}</div>
<p class="mono" style="margin-top:16px">favicon.ico (16/32/48) · favicon.svg · apple-touch-icon 180 · icon-192/512 ·
maskable-512 · og-image 1200×630</p>
<h2>Social preview</h2><img src="png/og-image.png" alt="" style="width:100%;border:1px solid var(--line)">
<h2>Installing in the web UI</h2>
<pre>&lt;link rel="icon" href="/favicon.ico" sizes="48x48"&gt;
&lt;link rel="icon" href="/favicon.svg" type="image/svg+xml"&gt;
&lt;link rel="apple-touch-icon" href="/apple-touch-icon.png"&gt;
&lt;link rel="manifest" href="/manifest.webmanifest"&gt;
&lt;meta name="theme-color" content="#04191a"&gt;
&lt;meta property="og:image" content="/og-image.png"&gt;</pre>
</main></body></html>"""
    (ROOT / "index.html").write_text(html, encoding="utf-8")


def copy_to_static() -> None:
    STATIC.mkdir(parents=True, exist_ok=True)
    mapping = {
        "favicon.ico": ROOT / "favicon.ico", "favicon.svg": ROOT / "favicon.svg",
        "apple-touch-icon.png": OUT_PNG / "apple-touch-icon.png",
        "icon-192.png": OUT_PNG / "icon-192.png", "icon-512.png": OUT_PNG / "icon-512.png",
        "maskable-512.png": OUT_PNG / "maskable-512.png", "og-image.png": OUT_PNG / "og-image.png",
        "manifest.webmanifest": ROOT / "manifest.webmanifest", "tokens.css": ROOT / "tokens.css",
        "logo.svg": ROOT / "lockup.svg", "logo-light.svg": ROOT / "lockup-light.svg",
        "mark.svg": ROOT / "mark.svg",
    }
    for dest, src in mapping.items():
        shutil.copyfile(src, STATIC / dest)


if __name__ == "__main__":
    svgs = write_svgs()
    write_text_files()
    write_rasters(svgs)
    write_guide(svgs)
    copy_to_static()
    print("brand assets written to", ROOT)
