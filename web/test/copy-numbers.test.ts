/**
 * No hard-coded numbers in copy.
 *
 * A benchmark site that types a count or a percentage into its copy will eventually show a number that is not
 * true. Every count, percentage and score on clipbench must be computed from web/public/data/*.json or the
 * Worker API at render time. This test fails when a source file under web/src contains:
 *   - a percentage literal:            "32%", "9.2 %"
 *   - a count phrase:                  "10 clips", "6 models", "1,200 votes", "ten clips"
 * unless the exact match is listed in ALLOW below with the reason it is a fixed methodology constant, not a
 * measurement. Template expressions like `${n} clips` are fine: the number comes from data.
 *
 * To add an exception, add the exact text and a reason. Do not loosen the patterns.
 */
import { readFileSync, readdirSync, statSync, existsSync } from "node:fs";
import { join, relative } from "node:path";
import { describe, expect, it } from "vitest";

const ROOT = join(__dirname, "..");
const SRC = join(ROOT, "src");

const UNITS = "clips?|models?|votes?|providers?|channels?|languages?|runs?|hours?|people|users?|requests?|videos?|shorts|captions?|speakers?|words?";
const NUMBER_WORDS = "two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|twenty|thirty|forty|fifty|hundred|thousand|dozen";

const PATTERNS: { name: string; re: RegExp }[] = [
  // Percentages, not inside CSS (w-[30%], black_40%, translate(-50%...)).
  { name: "percentage", re: /(?<![\w.$[_(\-/])\d+(?:\.\d+)?\s?%/g },
  // "<digits> <unit>"
  { name: "count", re: new RegExp(`(?<![\\w.$[_\\-/])\\d[\\d,]*(?:\\.\\d+)?\\s+(?:${UNITS})\\b`, "gi") },
  // "<number word> <unit>"
  { name: "count (word)", re: new RegExp(`\\b(?:${NUMBER_WORDS})\\s+(?:${UNITS})\\b`, "gi") },
];

/** Methodology constants that are rules of the benchmark, not results. Exact text, with the reason. */
const ALLOW: Record<string, string> = {
  "95%": "confidence level of every interval (METHODOLOGY.md, Confidence intervals)",
  "15 clips": "small-slice rule: slices under 15 clips are marked too small (METHODOLOGY.md, Slices)",
  "100%": "the 404 page's joke: nothing heard is a CER of 100% by definition, not a measurement",
  "2.5%": "lower percentile of the 95% bootstrap interval (methodology figure)",
  "97.5%": "upper percentile of the 95% bootstrap interval (methodology figure)",
  "30 requests": "API rate limit, anonymous (worker/src/ratelimit.ts LIMITS), stated in the API docs",
  "600 requests": "API rate limit, with a key (worker/src/ratelimit.ts LIMITS), stated in the API docs",
  "1,000 hours": "fixed volume used to illustrate list price on a model page, not a measurement",
};

function files(dir: string): string[] {
  return readdirSync(dir).flatMap((f) => {
    const p = join(dir, f);
    return statSync(p).isDirectory() ? files(p) : /\.(tsx?|css)$/.test(f) ? [p] : [];
  });
}

/** Drop comments (they explain rules and may cite numbers) but keep line numbers. */
function stripComments(src: string) {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, " "))
    .replace(/(^|[^:"'`])\/\/.*$/gm, (m, p1: string) => p1 + " ".repeat(m.length - p1.length));
}

function scan(): string[] {
  const hits: string[] = [];
  for (const f of files(SRC)) {
    if (f.endsWith(".css")) continue;
    const text = stripComments(readFileSync(f, "utf8"));
    const lines = text.split("\n");
    lines.forEach((line, i) => {
      // className strings and font-stretch values are styling, not copy.
      const copy = line
        .replace(/className=(\{`[^`]*`\}|"[^"]*")/g, "")
        .replace(/cn\([^)]*\)/g, "")
        .replace(/fontStretch:\s*"\d+%"/g, "");
      for (const { name, re } of PATTERNS) {
        for (const m of copy.matchAll(re)) {
          const t = m[0].replace(/\s+/g, " ").trim();
          if (ALLOW[t] || ALLOW[t.toLowerCase()]) continue;
          hits.push(`${relative(ROOT, f)}:${i + 1}  ${name}: "${t}"   ${line.trim().slice(0, 120)}`);
        }
      }
    });
  }
  return hits;
}

describe("copy never types a measured number", () => {
  it("src has no hard-coded percentages or counts outside the allowlist", () => {
    const hits = scan();
    expect(hits, `Hard-coded numbers in copy. Compute them from data, or allowlist a methodology constant in test/copy-numbers.test.ts:\n${hits.join("\n")}`).toEqual([]);
  });

  it("every allowlist entry is still used (no stale exceptions)", () => {
    const all = files(SRC)
      .filter((f) => !f.endsWith(".css"))
      .map((f) => stripComments(readFileSync(f, "utf8")))
      .join("\n");
    const unused = Object.keys(ALLOW).filter((k) => !all.includes(k));
    expect(unused, `Remove unused allowlist entries: ${unused.join(", ")}`).toEqual([]);
  });
});

describe("production build", () => {
  const dist = join(ROOT, "dist");
  const built = existsSync(join(dist, "index.html"));
  it.skipIf(!built)("ships no synthetic fixture data and no demo switch", () => {
    const assets = files(join(dist, "assets")).concat(existsSync(join(dist, "data")) ? files(join(dist, "data")) : []);
    const js = readdirSync(join(dist, "assets"))
      .filter((f) => f.endsWith(".js"))
      .map((f) => readFileSync(join(dist, "assets", f), "utf8"))
      .join("\n");
    expect(existsSync(join(dist, "data", "sample")), "dist/data/sample must not exist").toBe(false);
    expect(js.includes("model-a-v1"), "fixture provider ids found in the bundle").toBe(false);
    expect(/DEMO[A-Za-z0-9]{7}/.test(js), "fixture clip ids found in the bundle").toBe(false);
    expect(js.includes("test/fixtures"), "fixture paths found in the bundle").toBe(false);
    expect(js.includes("every number is synthetic"), "the dev-only demo banner leaked into the bundle").toBe(false);
    expect(assets.length).toBeGreaterThan(0);
  });

  it.skipIf(!built)("index.html has no hard-coded measurements", () => {
    const html = readFileSync(join(dist, "index.html"), "utf8").replace(/<script[\s\S]*?<\/script>/g, "").replace(/<style[\s\S]*?<\/style>/g, "");
    const text = html.replace(/<[^>]+>/g, " ");
    const hits = PATTERNS.flatMap(({ re }) => [...text.matchAll(re)].map((m) => m[0])).filter((t) => !ALLOW[t]);
    expect(hits).toEqual([]);
  });
});
