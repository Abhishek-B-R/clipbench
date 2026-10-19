"""Render report blocks to one static page: site/index.html.

No server: the page works opened from disk and deployed to Cloudflare Pages as is. Chart specs are
inlined and drawn by vega-embed from pinned jsdelivr versions; each chart starts as its PNG export
(site/charts/*.png) so the page still shows every chart offline or without JavaScript.
"""

from __future__ import annotations

import html
import json

from clipbench.report.report import METHODOLOGY_DOC, Block, RunData

VEGA = "https://cdn.jsdelivr.net/npm/vega@5.30.0"
VEGA_LITE = "https://cdn.jsdelivr.net/npm/vega-lite@5.21.0"
VEGA_EMBED = "https://cdn.jsdelivr.net/npm/vega-embed@6.26.0"

CSS = """
:root {
  color-scheme: light;
  --bg: #fcfcfb; --surface: #ffffff; --text: #0b0b0b; --muted: #52514e; --faint: #8a8984;
  --border: #e4e3de; --accent: #2a78d6; --draft-bg: #fff6e0; --draft-text: #6b4b00;
  --note-bg: #f1f0ec; --row-alt: #f7f7f4;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --bg: #141413; --surface: #1a1a19; --text: #f4f3ee; --muted: #c3c2b7; --faint: #8f8e86;
    --border: #2e2d2a; --accent: #3987e5; --draft-bg: #2e2510; --draft-text: #f0c66a;
    --note-bg: #222220; --row-alt: #1e1e1c;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --bg: #141413; --surface: #1a1a19; --text: #f4f3ee; --muted: #c3c2b7; --faint: #8f8e86;
  --border: #2e2d2a; --accent: #3987e5; --draft-bg: #2e2510; --draft-text: #f0c66a;
  --note-bg: #222220; --row-alt: #1e1e1c;
}
* { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; }
body {
  margin: 0; background: var(--bg); color: var(--text);
  font: 16px/1.6 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
}
main { max-width: 900px; margin: 0 auto; padding: 32px 16px 64px; }
header { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; }
h1 { font-size: 1.9rem; line-height: 1.2; margin: 0 0 4px; letter-spacing: -0.01em; }
h2 { font-size: 1.35rem; margin: 48px 0 12px; padding-top: 16px; border-top: 1px solid var(--border); }
h3 { font-size: 1.05rem; margin: 24px 0 8px; color: var(--muted); }
p, li { max-width: 72ch; }
.lede { font-size: 1.1rem; color: var(--muted); }
.note { background: var(--note-bg); padding: 10px 14px; border-radius: 8px; color: var(--muted); }
a { color: var(--accent); }
nav { font-size: 0.9rem; margin: 16px 0 0; display: flex; flex-wrap: wrap; gap: 4px 16px; }
nav a { text-decoration: none; }
.table-wrap { overflow-x: auto; -webkit-overflow-scrolling: touch; margin: 12px 0 4px;
  border: 1px solid var(--border); border-radius: 8px; background: var(--surface); }
table { border-collapse: collapse; width: 100%; font-size: 0.92rem; font-variant-numeric: tabular-nums; }
th, td { padding: 8px 12px; text-align: left; border-bottom: 1px solid var(--border); white-space: nowrap; }
td.wrap { white-space: normal; min-width: 16ch; }
th { font-weight: 600; color: var(--muted); background: var(--note-bg); }
tr:last-child td { border-bottom: none; }
tbody tr:nth-child(even) td { background: var(--row-alt); }
table.headline tbody tr:first-child td { font-weight: 600; }
table.unverified th { background: var(--draft-bg); color: var(--draft-text); }
.caption { font-size: 0.82rem; color: var(--faint); margin: 4px 0 20px; }
figure { margin: 24px 0; }
.chart { width: 100%; overflow-x: auto; background: var(--surface); border: 1px solid var(--border);
  border-radius: 8px; padding: 12px; }
.chart img { max-width: 100%; height: auto; display: block; }
figcaption { font-size: 0.82rem; color: var(--faint); margin-top: 6px; }
ul.findings { list-style: none; padding: 0; }
ul.findings li { background: var(--draft-bg); color: var(--draft-text); border-radius: 8px;
  padding: 8px 12px; margin: 8px 0; font-size: 0.95rem; }
#theme { font: inherit; font-size: 0.85rem; background: var(--surface); color: var(--muted);
  border: 1px solid var(--border); border-radius: 999px; padding: 4px 12px; cursor: pointer; }
footer { margin-top: 48px; font-size: 0.82rem; color: var(--faint); }
@media (max-width: 600px) { h1 { font-size: 1.5rem; } th, td { padding: 6px 8px; } }
"""

JS = """
(function () {
  var root = document.documentElement, btn = document.getElementById('theme');
  try { var saved = localStorage.getItem('clipbench-theme'); if (saved) root.dataset.theme = saved; } catch (e) {}
  function isDark() {
    return root.dataset.theme ? root.dataset.theme === 'dark'
      : window.matchMedia('(prefers-color-scheme: dark)').matches;
  }
  function label() { btn.textContent = isDark() ? 'Light mode' : 'Dark mode'; }
  btn.addEventListener('click', function () {
    root.dataset.theme = isDark() ? 'light' : 'dark';
    try { localStorage.setItem('clipbench-theme', root.dataset.theme); } catch (e) {}
    label();
  });
  label();
  if (typeof vegaEmbed !== 'function') return;
  var specs = JSON.parse(document.getElementById('chart-specs').textContent);
  Object.keys(specs).forEach(function (name) {
    var el = document.getElementById('chart-' + name);
    if (!el) return;
    var holder = document.createElement('div');
    vegaEmbed(holder, specs[name], { actions: false, renderer: 'svg' })
      .then(function () { el.innerHTML = ''; el.appendChild(holder); })
      .catch(function () { /* keep the PNG */ });
  });
})();
"""


def _e(s: object) -> str:
    return html.escape(str(s), quote=True)


def _slug(s: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in s.lower()).strip("-")


def _table(b: Block) -> str:
    head = "".join(f'<th scope="col">{_e(h)}</th>' for h in b.headers)
    body = []
    for r in b.rows:
        cells = "".join(f"<td{' class="wrap"' if len(str(c)) > 40 else ''}>{_e(c)}</td>" for c in r)
        body.append(f"<tr>{cells}</tr>")
    cls = f' class="{_e(b.css)}"' if b.css else ""
    cap = f'<p class="caption">{_e(b.caption)}</p>' if b.caption else ""
    return (
        f'<div class="table-wrap"><table{cls}><thead><tr>{head}</tr></thead>'
        f"<tbody>{''.join(body)}</tbody></table></div>{cap}"
    )


def render_html(data: RunData, blocks: list[Block]) -> str:
    parts: list[str] = []
    specs: dict[str, dict] = {}
    toc: list[tuple[str, str]] = []
    for b in blocks:
        if b.kind == "h2":
            sid = _slug(b.text)
            toc.append((sid, b.text))
            parts.append(f'<h2 id="{sid}">{_e(b.text)}</h2>')
        elif b.kind == "h3":
            parts.append(f"<h3>{_e(b.text)}</h3>")
        elif b.kind == "p":
            text = _e(b.text)
            if b.name and b.name.startswith("http"):
                text = text.replace(_e(b.name), f'<a href="{_e(b.name)}">{_e(b.name)}</a>')
            elif METHODOLOGY_DOC in b.text:
                text = text.replace(METHODOLOGY_DOC, f"<code>{METHODOLOGY_DOC}</code>")
            cls = f' class="{_e(b.css)}"' if b.css else ""
            parts.append(f"<p{cls}>{text}</p>")
        elif b.kind == "list":
            cls = f' class="{_e(b.css)}"' if b.css else ""
            parts.append(f"<ul{cls}>" + "".join(f"<li>{_e(i)}</li>" for i in b.items) + "</ul>")
        elif b.kind == "table":
            parts.append(_table(b))
        elif b.kind == "chart":
            specs[b.name] = b.spec or {}
            parts.append(
                f'<figure><div class="chart" id="chart-{_e(b.name)}">'
                f'<img src="charts/{_e(b.name)}.png" alt="{_e(b.text)}" loading="lazy"></div>'
                f"<figcaption>{_e(b.caption)}</figcaption></figure>"
            )
    nav = "".join(f'<a href="#{sid}">{_e(t)}</a>' for sid, t in toc)
    # Keep the inline JSON safe inside <script>: escape "</" so no spec string can close the tag.
    spec_json = json.dumps(specs, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    title = f"clipbench: {data.run_id}"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>clipbench report</title>
<meta name="description" content="Speech-to-text models on real short-form video: accuracy, cost, failures.">
<style>{CSS}</style>
</head>
<body>
<main>
<header>
<div><h1>{_e(title)}</h1></div>
<button id="theme" type="button">Dark mode</button>
</header>
<nav aria-label="Sections">{nav}</nav>
{"".join(parts)}
<footer>Generated by clipbench. No audio and no full captions are published.</footer>
</main>
<script id="chart-specs" type="application/json">{spec_json}</script>
<script src="{VEGA}"></script>
<script src="{VEGA_LITE}"></script>
<script src="{VEGA_EMBED}"></script>
<script>{JS}</script>
</body>
</html>
"""
