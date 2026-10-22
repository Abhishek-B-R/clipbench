/** Publication gate (docs/V2.md "Languages", docs/V3.md "Publication gate fields"): read from the export, fail closed without it. */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import type { Run, RunIndex } from "@/lib/types";
import { isPublished, languageProgress, runLanguageStatus } from "@/lib/analysis";

const fixture = (p: string) => JSON.parse(readFileSync(join(__dirname, "fixtures", "sample", p), "utf8"));
const index: RunIndex = fixture("index.json");
const full: Run = fixture(`runs/${index.latest}.json`);

/** A real-shaped early run: creator captions only, two providers. */
function earlyRun(): Run {
  const clips = full.clips.filter((c) => c.tier === "creator").slice(0, 10);
  const ids = new Set(clips.map((c) => c.video_id));
  const providers = full.providers.slice(0, 2);
  const keep = new Set(providers.map((p) => p.id));
  return { ...full, languages: undefined, providers, clips, per_clip: full.per_clip.filter((r) => ids.has(r.video_id) && keep.has(r.provider)) };
}

describe("publication gate", () => {
  it("publishes a language with enough verified clips and providers", () => {
    const s = runLanguageStatus(full);
    expect(s.verified_clips).toBeGreaterThanOrEqual(100);
    expect(s.providers_ok).toBeGreaterThanOrEqual(6);
    expect(isPublished(full)).toBe(true);
  });

  it("never publishes from client-side counts when the exporter's status is missing", () => {
    const s = runLanguageStatus({ ...full, languages: undefined });
    expect(s.published).toBe(false);
    expect(s.providers_ok).toBe(0);
    expect(isPublished({ ...full, languages: [{ lang: full.language, verified_clips: 150, providers_ok: 6, published: true }] })).toBe(false);
  });

  it("does not publish an early creator-only run", () => {
    const s = runLanguageStatus(earlyRun());
    expect(s.verified_clips).toBe(0);
    expect(s.published).toBe(false);
  });

  it("prefers the exporter's languages[] when present", () => {
    const run = { ...full, languages: [{ lang: full.language, verified_clips: 3, providers_ok: 1, published: false }] };
    expect(isPublished(run)).toBe(false);
  });

  it("lists every v2 language from the index, unpublished when it has no run", () => {
    const rows = languageProgress({ latest: "x", runs: [{ run_id: "x", created_at: "2026-10-19T00:00:00Z", language: "ja", tiers: ["creator"], n_clips: { creator: 10 }, providers: ["a", "b"] }] });
    expect(rows.map((r) => r.lang)).toEqual(["en", "ja", "ko", "es", "hi", "fr"]);
    expect(rows.every((r) => !r.published)).toBe(true);
    expect(rows.find((r) => r.lang === "ja")?.scored).toBe(10);
    expect(rows.find((r) => r.lang === "en")?.latestRun).toBeUndefined();
  });
});
