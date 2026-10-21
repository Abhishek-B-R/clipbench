/** Post-build share metadata (scripts/og-routes.ts), run against a temp dist built from the test fixture. */
import { execFileSync } from "node:child_process";
import { cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const ROOT = join(__dirname, "..");
const FIX = join(__dirname, "fixtures", "sample");

describe("og-routes", () => {
  it("writes a page per language, per findings page and per finding, with its own title and image", () => {
    const dist = mkdtempSync(join(process.env.TMPDIR ?? tmpdir(), "og-"));
    writeFileSync(join(dist, "index.html"), `<!doctype html><html><head><title>x</title><meta name="description" content="x" /></head><body><div id="root"></div></body></html>`);
    mkdirSync(join(dist, "data", "findings"), { recursive: true });
    cpSync(join(FIX, "index.json"), join(dist, "data", "index.json"));
    cpSync(join(FIX, "runs"), join(dist, "data", "runs"), { recursive: true });
    cpSync(join(FIX, "findings", "ja.json"), join(dist, "data", "findings", "ja.json"));
    mkdirSync(join(dist, "og"), { recursive: true });
    writeFileSync(join(dist, "og", "home.png"), "");
    execFileSync("bun", ["run", join(ROOT, "scripts", "og-routes.ts")], { env: { ...process.env, OG_DIST: dist, SITE_URL: "https://bench.test" } });

    const home = readFileSync(join(dist, "index.html"), "utf8");
    expect(home).toContain('<meta property="og:image" content="https://bench.test/og/home.png" />');
    expect(home.match(/<title>/g)?.length).toBe(1);

    const findings = JSON.parse(readFileSync(join(FIX, "findings", "ja.json"), "utf8")) as { findings: { slug: string; text: string }[] };
    const first = findings.findings[0]!;
    const page = readFileSync(join(dist, "findings", "ja", `${first.slug}.html`), "utf8");
    expect(page).toContain(`<link rel="canonical" href="https://bench.test/findings/ja/${first.slug}" />`);
    expect(page).toContain("twitter:card");
    expect(existsSync(join(dist, "lang", "ja.html"))).toBe(true);
    expect(existsSync(join(dist, "findings", "en.html"))).toBe(true);

    // Every top-level page and every model page gets its own canonical, so a shared link never previews as "/".
    for (const route of ["leaderboard", "compare", "picker", "clips", "methodology", "changelog", "docs/api"]) {
      const html = readFileSync(join(dist, `${route}.html`), "utf8");
      expect(html).toContain(`<link rel="canonical" href="https://bench.test/${route}" />`);
      expect(html).toContain(`<meta property="og:url" content="https://bench.test/${route}" />`);
    }
    const run = JSON.parse(readFileSync(join(FIX, "runs", `${JSON.parse(readFileSync(join(FIX, "index.json"), "utf8")).latest}.json`), "utf8")) as { providers: { id: string; label: string }[] };
    const p = run.providers[0]!;
    const model = readFileSync(join(dist, "providers", `${p.id}.html`), "utf8");
    expect(model).toContain(`<title>${p.label} | clipbench</title>`);
    expect(model).not.toMatch(/\d+(\.\d+)?%/);

    // robots.txt and sitemap.xml come from the same route list, with absolute SITE_URL links.
    const sitemap = readFileSync(join(dist, "sitemap.xml"), "utf8");
    expect(sitemap).toContain("<loc>https://bench.test/</loc>");
    expect(sitemap).toContain(`<loc>https://bench.test/findings/ja/${first.slug}</loc>`);
    expect(sitemap).toContain(`<loc>https://bench.test/providers/${p.id}</loc>`);
    expect(sitemap).not.toContain("/api/");
    expect(readFileSync(join(dist, "robots.txt"), "utf8")).toContain("Sitemap: https://bench.test/sitemap.xml");
  });
});
