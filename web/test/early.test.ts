/** Early look (src/lib/early.ts): every system, sorted, no ranks, and it never opens the publication gate. */
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import type { Run, RunIndex } from "@/lib/types";
import { isPublished, overall, primaryMetric, ranking } from "@/lib/analysis";
import { EARLY_LABEL, clipScores, earlyLook, flaggedClips, latencyMode, partialCause, partialReason, runSpend, scoredTranscriptions, thumbUrl, videoIdOf } from "@/lib/early";

const sample = <T>(p: string): T => JSON.parse(readFileSync(join(__dirname, "fixtures", "sample", p), "utf8"));
const index = sample<RunIndex>("index.json");
const full = sample<Run>(`runs/${index.latest}.json`);

/** A creator-only, unpublished run like the first real one. */
function early(): Run {
  const clips = full.clips.filter((c) => c.tier === "creator");
  return { ...full, clips, languages: [{ lang: full.language, verified_clips: 0, providers_ok: 0, published: false, gate: { verified_clips_ok: false, providers_ok: false, diversity_ok: false } }], ranking: {} };
}

const REAL = join(__dirname, "..", "public", "data");
const real: Run | null = (() => {
  if (!existsSync(join(REAL, "index.json"))) return null;
  const idx = JSON.parse(readFileSync(join(REAL, "index.json"), "utf8")) as RunIndex;
  const p = join(REAL, "runs", `${idx.latest}.json`);
  return existsSync(p) ? (JSON.parse(readFileSync(p, "utf8")) as Run) : null;
})();

describe("early look", () => {
  it("lists every system on the tier, sorted by error rate, without touching the ranking", () => {
    const run = early();
    const look = earlyLook(run, "creator");
    const metric = primaryMetric(run).name;
    const systems = new Set(run.aggregates.filter((a) => a.tier === "creator" && a.metric === metric && a.slice_kind === "all").map((a) => a.provider));
    expect(look.rows.map((r) => r.provider).sort()).toEqual([...systems].sort());
    for (let i = 1; i < look.rows.length; i++) expect(look.rows[i]!.error_rate).toBeGreaterThanOrEqual(look.rows[i - 1]!.error_rate);
    expect(look.rows.every((r) => !("rank" in r))).toBe(true);
    expect(look.published).toBe(false);
    expect(isPublished(run)).toBe(false);
    expect(ranking(run, "creator", metric)).toEqual([]);
    expect(overall(run, "creator", metric)).toEqual([]);
  });

  it("carries the persistent label word for word", () => {
    expect(EARLY_LABEL).toBe("Early look: scored against creator captions, which are not verified. Not a ranking.");
    expect(/[\u2013\u2014]/.test(EARLY_LABEL)).toBe(false);
  });

  it("hides latency unless the run timed one provider at a time", () => {
    const run = early();
    const seq = { ...run, run_meta: { latency_mode: { sequential_providers: true, contended: false, text: "one at a time" } } } as Run;
    const par = { ...run, run_meta: { latency_mode: { sequential_providers: false, contended: true } } } as Run;
    expect(latencyMode(seq).sequential).toBe(true);
    expect(earlyLook(seq, "creator").rows.some((r) => r.latency_p50 != null)).toBe(true);
    expect(latencyMode(par).sequential).toBe(false);
    expect(earlyLook(par, "creator").rows.every((r) => r.latency_p50 == null)).toBe(true);
    expect(latencyMode({ ...run, run_meta: null } as Run).sequential).toBeNull();
  });

  it("reads the partial-coverage reason from the exporter's sentence", () => {
    const run = {
      ...early(),
      languages: [{ lang: full.language, verified_clips: 0, providers_ok: 0, published: false, providers_excluded: [{ provider: "x", coverage: null, n_scored: 0, n_skipped: 0, n_error: 0, reason: "no verified clips in this run; on the clips it was given: not run on 122 (account quota)" }] }],
    } as Run;
    expect(partialReason(run, "x")).toBe("not run on 122 (account quota)");
    expect(partialCause(partialReason(run, "x"))).toBe("account quota");
    expect(partialReason(run, "y")).toBeNull();
    expect(partialCause(null)).toBeNull();
  });

  it("flags a clip only when every system with a result is above the threshold, and prefers the export's list", () => {
    const run = early();
    const f = flaggedClips(run, "creator");
    expect(f.source).toBe("per_clip");
    const metric = primaryMetric(run).name;
    for (const id of f.ids) {
      const xs = run.per_clip.filter((r) => r.video_id === id && r.tier === "creator" && r.metric === metric).map((r) => r.error_rate);
      expect(xs.length).toBeGreaterThanOrEqual(2);
      expect(xs.every((x) => x > f.threshold)).toBe(true);
    }
    const listed = { ...run, errors: { ...run.errors, all_fail: ["a", "b"], all_fail_threshold: 0.6 } } as Run;
    expect([...flaggedClips(listed, "creator").ids]).toEqual(["a", "b"]);
    expect(flaggedClips(listed, "creator").threshold).toBe(0.6);
  });

  it("orders clips hardest first and builds public thumbnail urls from the platform id", () => {
    const xs = clipScores(early(), "creator");
    for (let i = 1; i < xs.length; i++) if (!Number.isNaN(xs[i]!.med) && !Number.isNaN(xs[i - 1]!.med)) expect(xs[i]!.med).toBeLessThanOrEqual(xs[i - 1]!.med);
    expect(videoIdOf("abc@1000-2000")).toBe("abc");
    expect(thumbUrl("abc@1000-2000")).toBe("https://i.ytimg.com/vi/abc/mqdefault.jpg");
  });
});

describe.skipIf(!real)("early look on the real export", () => {
  it("covers every system of the latest run and stays unranked while unpublished", () => {
    const run = real!;
    const look = earlyLook(run);
    expect(look.rows.length).toBe(new Set(run.aggregates.filter((a) => a.tier === look.tier && a.metric === look.metric && a.slice_kind === "all").map((a) => a.provider)).size);
    if (!isPublished(run)) expect(ranking(run, look.tier, look.metric)).toEqual([]);
    for (const r of look.rows) if (r.n_clips < r.total) expect(r.partial, r.provider).not.toBeNull();
    expect(runSpend(run)).toBeGreaterThan(0);
    expect(scoredTranscriptions(run)).toBeGreaterThan(0);
  });
});
