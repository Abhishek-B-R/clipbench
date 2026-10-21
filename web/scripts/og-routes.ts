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
 *   /leaderboard, /compare, /picker, /clips, /methodology, /changelog, /docs/api   dist/<page>.html
 *   /providers/<id>            dist/providers/<id>.html           (every provider in any exported run)
 *   sitemap.xml and robots.txt (every route above, absolute with SITE_URL)
 * Without its own file a route falls back to index.html, whose canonical and og:url point at "/": a shared link
 * would preview as the homepage and crawlers would fold the page into it.
 * Files are "<route>.html" rather than "<route>/index.html" because Workers static assets
 * (html_handling: auto-trailing-slash) serve /lang/ja from lang/ja.html without redirecting to /lang/ja/.
 *
 * Every number in a description comes from dist/data (index.json, findings/<lang>.json). Images come from
 * dist/og (written by the exporter); a route without its own image falls back to og/home.png, or none.
 * SITE_URL sets the absolute origin (default https://clipbench.abhishekbr.workers.dev, the production domain).
 *
 * Run: bun run scripts/og-routes.ts   (part of `bun run build`)
 */
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { LANGUAGES, POSITIONING, RULES } from "../src/lib/config";
import { LANGUAGE_NAME, languageProgress } from "../src/lib/analysis";
import type { FindingsFile, Run, RunIndex } from "../src/lib/types";

const DIST = process.env.OG_DIST ?? join(import.meta.dir, "..", "dist");
const SITE = (process.env.SITE_URL ?? "https://clipbench.abhishekbr.workers.dev").replace(/\/$/, "");
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
  // providers_ok is the exporter's count of ELIGIBLE providers (docs/V3.md, "Publication gate fields"), never every
  // provider that was run, so the description says "eligible" and an unpublished language never reads as ranked.
  const eligible = (n: number) => `${n} eligible ${n === 1 ? "provider" : "providers"}`;
  const status = st.published
    ? `Published: ${st.verified_clips} verified clips, ${eligible(st.providers_ok)}.`
    : `First results in progress, no ranking yet: ${st.verified_clips} of ${RULES.publishMinVerified} verified clips, ${st.providers_ok} of ${RULES.publishMinProviders} eligible providers.`;
  metas.push({
    route: `/lang/${lang}`,
    title: st.published ? `${name} speech-to-text results | clipbench` : `${name} speech-to-text progress | clipbench`,
    description: `${status} ${LINE}`,
    image: image(`og/${lang}.png`),
  });

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

// Static pages. Descriptions state what the page is; no numbers, so they hold whether or not a language is published.
const PAGES: [string, string, string][] = [
  ["/leaderboard", "Leaderboard", "Speech-to-text models ranked per language on real short-form video, with 95% intervals, once a language passes the publication gate."],
  ["/streaming", "Streaming leaderboard", "Realtime speech-to-text APIs on real short-form video: time to first partial, time to final segment, partial stability and final accuracy, never mixed with batch."],
  ["/features", "Speech-to-text feature matrix", "What each speech-to-text provider offers: languages, streaming, word timestamps, diarization, language ID, custom vocabulary, limits, retention and price, each linked to its docs."],
  ["/compare", "Compare models", "Compare speech-to-text models on the same short-form clips. Verdicts come from a paired test, not from the gap between two numbers."],
  ["/picker", "Which speech-to-text for my app?", "Pick a speech-to-text provider for your clips: music, budget per audio hour, word timing, latency and local-only constraints."],
  ["/clips", "Clip set", "Every clip in the benchmark by its public id, with tags. Captions are never published; anyone can rebuild the set."],
  ["/methodology", "Methodology", "How clipbench scores speech-to-text on short-form video: references, normalisation, metrics, intervals and paired tests."],
  ["/changelog", "Changelog", "Every clipbench run over time."],
  ["/docs/api", "Data API", "Every published clipbench run as JSON: the same numbers the site shows, with intervals, paired tests and per-clip scores."],
];
for (const [route, title, description] of PAGES) metas.push({ route, title: `${title} | clipbench`, description, image: image() });

// Model pages: one per provider id in any exported run, labelled from the first run in index.json that has it.
const labels = new Map<string, string>();
for (const r of index?.runs ?? []) {
  if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$/.test(r.run_id)) continue;
  const run = readJSON<Run>(join(DIST, "data", "runs", `${r.run_id}.json`));
  for (const p of run?.providers ?? []) if (!labels.has(p.id)) labels.set(p.id, p.label || p.id);
}
for (const [id, label] of labels) {
  if (!/^[a-z0-9][a-z0-9._-]{0,63}$/.test(id)) continue;
  metas.push({ route: `/providers/${id}`, title: `${label} | clipbench`, description: `${label} on real short-form video: accuracy, word timing, hallucination over music and cost, per language.`, image: image() });
}

for (const m of metas) write(m.route, render(base, m));

// robots.txt and sitemap.xml from the same route list, so they never drift from the pages that exist.
// The API is for programs, not search results; /data stays crawlable (it is the published JSON).
const xmlEsc = (x: string) => x.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const today = new Date().toISOString().slice(0, 10);
const lastmod = index?.runs?.map((r) => r.created_at).sort().at(-1)?.slice(0, 10) ?? today;
writeFileSync(
  join(DIST, "sitemap.xml"),
  `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n${metas
    .map((m) => `  <url><loc>${xmlEsc(`${SITE}${m.route}`)}</loc><lastmod>${lastmod}</lastmod></url>`)
    .join("\n")}\n</urlset>\n`,
);
writeFileSync(join(DIST, "robots.txt"), `User-agent: *\nAllow: /\nDisallow: /api/\n\nSitemap: ${SITE}/sitemap.xml\n`);
console.log(`og-routes: wrote ${metas.length} routes, sitemap.xml and robots.txt for ${SITE}`);
