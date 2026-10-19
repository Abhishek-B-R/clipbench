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
  });
});
