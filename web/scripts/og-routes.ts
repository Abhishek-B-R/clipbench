/**
 * Post-build: per-route share metadata for crawlers (Open Graph, Twitter cards).
 *
 * The site is a single-page app, so every route would otherwise share index.html's generic <head>. Crawlers
 * do not run JavaScript, so this writes a copy of dist/index.html per shareable route with its own <title>,
 * description, canonical URL and og:image:
 *   /                          dist/index.html
 *   /lang/<code>               dist/lang/<code>.html
 *   /findings/<code>           dist/findings/<code>.html
 *   /findings/<code>/<slug>    dist/findings/<code>/<slug>.html   (the SPA opens that finding highlighted)
 * Files are "<route>.html" rather than "<route>/index.html" because Workers static assets
 * (html_handling: auto-trailing-slash) serve /lang/ja from lang/ja.html without redirecting to /lang/ja/.
 *
 * Every number in a description comes from dist/data (index.json, findings/<lang>.json). Images come from
 * dist/og (written by the exporter); a route without its own image falls back to og/home.png, or none.
 * SITE_URL sets the absolute origin (default https://clipbench.example, a placeholder until a domain exists).
 *
 * Run: bun run scripts/og-routes.ts   (part of `bun run build`)
 */
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { LANGUAGES, POSITIONING, RULES } from "../src/lib/config";
import { LANGUAGE_NAME, languageProgress } from "../src/lib/analysis";
import type { FindingsFile, RunIndex } from "../src/lib/types";

const DIST = process.env.OG_DIST ?? join(import.meta.dir, "..", "dist");
const SITE = (process.env.SITE_URL ?? "https://clipbench.example").replace(/\/$/, "");
const LINE = "The independent benchmark for captions on real short-form video: accuracy, word timing, hallucination over music, and cost.";

const esc = (s: string) => s.replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const clip = (s: string, n: number) => (s.length <= n ? s : `${s.slice(0, n - 1).trimEnd()}...`);

function readJSON<T>(p: string): T | null {
  try {
    return JSON.parse(readFileSync(p, "utf8")) as T;
  } catch {
    return null;
  }
}

function image(...candidates: string[]) {
  for (const c of [...candidates, "og/home.png"]) if (existsSync(join(DIST, c))) return `${SITE}/${c}`;
  return null;
}

interface Meta {
  route: string;
  title: string;
  description: string;
  image: string | null;
}

function render(base: string, m: Meta) {
  const url = `${SITE}${m.route === "/" ? "/" : m.route}`;
  const tags = [
    `<title>${esc(m.title)}</title>`,
    `<meta name="description" content="${esc(m.description)}" />`,
    `<link rel="canonical" href="${esc(url)}" />`,
    `<meta property="og:type" content="website" />`,
    `<meta property="og:site_name" content="clipbench" />`,
    `<meta property="og:url" content="${esc(url)}" />`,
    `<meta property="og:title" content="${esc(m.title)}" />`,
    `<meta property="og:description" content="${esc(m.description)}" />`,
    ...(m.image ? [`<meta property="og:image" content="${esc(m.image)}" />`, `<meta property="og:image:width" content="1200" />`, `<meta property="og:image:height" content="630" />`] : []),
    `<meta name="twitter:card" content="${m.image ? "summary_large_image" : "summary"}" />`,
    `<meta name="twitter:title" content="${esc(m.title)}" />`,
    `<meta name="twitter:description" content="${esc(m.description)}" />`,
    ...(m.image ? [`<meta name="twitter:image" content="${esc(m.image)}" />`] : []),
  ].join("\n    ");
  return base
    .replace(/<title>[\s\S]*?<\/title>\s*/, "")
    .replace(/\s*<meta (name="description"|property="og:[^"]+"|name="twitter:[^"]+")[^>]*>/g, "")
    .replace(/\s*<link rel="canonical"[^>]*>/g, "")
    .replace("</head>", `    ${tags}\n  </head>`);
}

function write(route: string, html: string) {
  const file = route === "/" ? join(DIST, "index.html") : join(DIST, `${route.replace(/^\//, "")}.html`);
  mkdirSync(dirname(file), { recursive: true });
  writeFileSync(file, html);
  return file;
}

const indexPath = join(DIST, "index.html");
if (!existsSync(indexPath)) throw new Error("dist/index.html not found: run vite build first");
const base = readFileSync(indexPath, "utf8");
const index = readJSON<RunIndex>(join(DIST, "data", "index.json"));
const progress = languageProgress(index ?? undefined);

const metas: Meta[] = [{ route: "/", title: `clipbench: ${POSITIONING.question}`, description: LINE, image: image("og/home.png") }];

for (const lang of LANGUAGES) {
  const name = LANGUAGE_NAME[lang] ?? lang;
  const st = progress.find((p) => p.lang === lang)!;
  const status = st.published
    ? `Published: ${st.verified_clips} verified clips, ${st.providers_ok} providers.`
    : `First results in progress: ${st.verified_clips} of ${RULES.publishMinVerified} verified clips, ${st.providers_ok} of ${RULES.publishMinProviders} providers.`;
  metas.push({ route: `/lang/${lang}`, title: `${name} speech-to-text results | clipbench`, description: `${status} ${LINE}`, image: image(`og/${lang}.png`) });

  const file = readJSON<FindingsFile | FindingsFile["findings"]>(join(DIST, "data", "findings", `${lang}.json`));
  const findings = st.published && file ? (Array.isArray(file) ? file : file.findings ?? []) : [];
  metas.push({
    route: `/findings/${lang}`,
    title: `What we found in ${name} | clipbench`,
    description: findings[0] ? clip(findings[0].text, 200) : `${status} Findings appear once ${name} is published.`,
    image: image(`og/${lang}.png`),
  });
  for (const f of findings) {
    if (!/^[a-z0-9-]+$/.test(f.slug)) continue;
    metas.push({ route: `/findings/${lang}/${f.slug}`, title: `${clip(f.text, 90)} | clipbench`, description: clip(f.text, 200), image: image(`og/${lang}/${f.slug}.png`, `og/${lang}.png`) });
  }
}

for (const m of metas) write(m.route, render(base, m));
console.log(`og-routes: wrote ${metas.length} routes for ${SITE}`);
