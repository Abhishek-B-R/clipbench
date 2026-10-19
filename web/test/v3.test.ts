/** v3 export parsers (src/lib/v3.ts): every key optional and nullable, odd shapes read defensively, never thrown on. */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import type { Run, RunIndex } from "@/lib/types";
import { fallsApartAt, historyFor, inSlice, latticeFor, pricePer1000Min, reliabilityFor, safeUrl, settingsFor, speedFactor, streamPair, supports, v3 } from "@/lib/v3";

const fixture = <T>(p: string): T => JSON.parse(readFileSync(join(__dirname, "fixtures", "sample", p), "utf8"));
const index = fixture<RunIndex>("index.json");
const latest = fixture<Run>(`runs/${index.latest}.json`);
const KEYS = ["streaming", "semantic", "drift", "history", "sweeps", "formatting", "lattice", "longform", "compute", "diarization", "lid", "entities", "style", "reliability", "captions", "features"] as const;
const bare = (extra: Partial<Run>): Run => ({ ...latest, ...Object.fromEntries(KEYS.map((k) => [k, null])), run_meta: null, ...extra }) as Run;

describe("v3 sections", () => {
  it("reads every section of the synthetic fixture", () => {
    const x = v3(latest);
    for (const k of KEYS) if (k !== "lattice") expect(x[k], k).not.toBeNull();
    expect(x.streaming!.rows.length).toBeGreaterThan(0);
    expect(x.semantic!.published).toBe(true);
    expect(x.features!.providers.length).toBe(latest.providers.length);
  });

  it("treats null, missing and junk the same: no section, no throw", () => {
    for (const junk of [null, undefined, 3, "x", [], {}, [1, "a", null], { aggregates: "nope" }, { events: [{}] }]) {
      const x = v3(bare(Object.fromEntries(KEYS.map((k) => [k, junk])) as Partial<Run>));
      for (const k of KEYS) expect(x[k], `${k} <- ${JSON.stringify(junk)}`).toBeNull();
    }
  });

  it("older exports without the keys have no v3 data", () => {
    const old = { ...latest } as Record<string, unknown>;
    for (const k of KEYS) delete old[k];
    const x = v3(old as unknown as Run);
    for (const k of KEYS) expect(x[k]).toBeNull();
  });

  it("reads streaming rows under other field names, and percentages as shares", () => {
    const x = v3(bare({ streaming: [{ provider: "p", final_error_rate: 0.1, ci: [0.08, 0.12], ttfp_ms_p50: 300, time_to_final_ms_p50: 500, partial_rewrite_rate: 12 }] }));
    const r = x.streaming!.rows[0]!;
    expect(r).toMatchObject({ provider: "p", error_rate: 0.1, ci_low: 0.08, ci_high: 0.12, ttfp_p50_ms: 300, ttfs_p50_ms: 500, slice_kind: "all", slice: "all", tier: null });
    expect(r.stability).toBeCloseTo(0.88);
    expect(inSlice(x.streaming!.rows, "verified")).toHaveLength(1);
  });

  it("orients streaming pairs and says tied when the interval crosses zero", () => {
    const st = v3(latest).streaming!;
    const p = st.paired[0]!;
    const t = streamPair(st, "verified", p.slice_kind, p.slice, p.provider_b, p.provider_a)!;
    expect(t.diff).toBeCloseTo(-p.diff);
    expect(t.tied).toBe(p.verdict === "no_difference");
  });

  it("keeps semantic scores unpublished below the threshold, with the export's reason", () => {
    const x = v3(bare({ semantic: { calibration: { n_pairs: 50, agreement: 0.6, threshold: 0.8 }, reason: "Judge agreement is below the threshold.", aggregates: [{ provider: "p", score: 0.9 }] } }));
    expect(x.semantic!.published).toBe(false);
    expect(x.semantic!.reason).toBe("Judge agreement is below the threshold.");
    expect(x.semantic!.rows[0]!.error).toBeCloseTo(0.1);
    // An explicit published flag is still refused when there are no rows to show.
    expect(v3(bare({ semantic: { published: true, calibration: { agreement: 0.9, threshold: 0.8 } } })).semantic!.published).toBe(false);
  });

  it("applies the METHODOLOGY.md gate (pairs, Spearman, mean absolute error) per language", () => {
    const cal = (spearman: number, n_pairs: number) => ({ calibration: { ja: { n_pairs, spearman, mae: 0.1 }, thresholds: { min_pairs: 50, min_spearman: 0.7, max_mae: 0.2 } }, aggregates: [{ provider: "p", semantic_error: 0.1 }] });
    expect(v3(bare({ language: "ja", semantic: cal(0.8, 60) })).semantic!.published).toBe(true);
    expect(v3(bare({ language: "ja", semantic: cal(0.6, 60) })).semantic!.published).toBe(false);
    expect(v3(bare({ language: "ja", semantic: cal(0.8, 12) })).semantic!.published).toBe(false);
    // An explicit published: false from the exporter always wins.
    expect(v3(bare({ language: "ja", semantic: { ...cal(0.8, 60), published: false, reason: "12 labelled pairs, needs 50" } })).semantic!.reason).toBe("12 labelled pairs, needs 50");
  });

  it("reads drift with an interval object and infers direction", () => {
    const d = v3(bare({ drift: [{ provider: "p", date: "2026-09-07", before: 0.1, after: 0.14, ci: { low: 0.09, high: 0.11 } }] })).drift!;
    expect(d[0]).toMatchObject({ direction: "worse", ci_low: 0.09, ci_high: 0.11 });
  });

  it("reads history as a map, a list of series or flat points", () => {
    const pts = [{ date: "2026-09-01", error_rate: 0.1 }, { date: "2026-08-01", error_rate: 0.2 }];
    for (const h of [{ p: pts }, [{ provider: "p", points: pts }], pts.map((x) => ({ ...x, provider: "p" })), { series: { p: { points: pts } } }]) {
      const got = historyFor(v3(bare({ history: h })).history, "p");
      expect(got.map((x) => x.date)).toEqual(["2026-08-01", "2026-09-01"]);
    }
  });

  it("finds where a model falls apart from intervals when the export does not say", () => {
    const sweeps = { points: [
      { provider: "p", bed: "clean", error_rate: 0.05, ci_low: 0.04, ci_high: 0.06 },
      { provider: "p", bed: "music", snr_db: 10, error_rate: 0.055, ci_low: 0.045, ci_high: 0.065 },
      { provider: "p", bed: "music", snr_db: 0, error_rate: 0.09, ci_low: 0.07, ci_high: 0.11 },
      { provider: "p", bed: "music", snr_db: -5, error_rate: 0.2, ci_low: 0.15, ci_high: 0.25 },
    ] };
    expect(fallsApartAt(v3(bare({ sweeps })).sweeps!, "p", "music", "verified")).toBe(0);
    expect(fallsApartAt(v3(bare({ sweeps: { ...sweeps, falls_apart: [{ provider: "p", bed: "music", snr_db: 5 }] } })).sweeps!, "p", "music", "verified")).toBe(5);
  });

  it("reads the feature matrix flat (pricing.toml), nested, or long", () => {
    const flat = v3(bare({ features: { a: { streaming: true, streaming_url: "https://x.test/s", streaming_checked_on: "2026-10-01" } } })).features!;
    expect(flat.providers[0]!.cells.streaming).toMatchObject({ support: "yes", url: "https://x.test/s", checked_on: "2026-10-01" });
    const nested = v3(bare({ features: { providers: { a: { diarization: { value: false, url: "https://x.test/d" } } } } })).features!;
    expect(supports(nested, "a", "diarization")).toBe(false);
    const long = v3(bare({ features: [{ provider: "a", key: "language_id", value: "beta", url: "javascript:alert(1)" }] })).features!;
    expect(long.providers[0]!.cells.language_id).toMatchObject({ support: "partial", url: null });
  });

  it("reads entities in long (mode) and wide form", () => {
    const longE = v3(bare({ entities: [{ provider: "a", mode: "plain", entity_acc: 0.7 }, { provider: "a", mode: "keyterms", entity_acc: 0.8 }] })).entities!;
    expect(longE.rows[0]).toMatchObject({ plain: { acc: 0.7 }, keyterms: { acc: 0.8 } });
    const wide = v3(bare({ entities: [{ provider: "a", entity_acc: 0.7, entity_acc_keyterms: 0.8 }] })).entities!;
    expect(wide.rows[0]!.keyterms?.acc).toBe(0.8);
  });

  it("splits reliability into this run and all runs", () => {
    const rel = v3(bare({ reliability: { run: [{ provider: "a", failure_rate: 0.01 }], all_runs: [{ provider: "a", failure_rate: 0.02, p99_latency_ms: 4000 }] } })).reliability;
    const r = reliabilityFor(rel, "a");
    expect(r.run?.failure_rate).toBe(0.01);
    expect(r.all?.p99_ms).toBe(4000);
  });

  it("never prints a credential from the run settings", () => {
    const run = bare({ run_meta: { options: { diarize: true }, providers: { a: { model: "m", api_key: "sk-123", auth_token: "t" } } } });
    const s = settingsFor(run, "a");
    expect(s).toContainEqual(["diarize", "true"]);
    expect(s).toContainEqual(["model", "m"]);
    expect(JSON.stringify(s)).not.toMatch(/sk-123|auth_token|api_key/);
  });

  it("derives lattice, speed factor and price per 1,000 minutes from the run", () => {
    const lat = latticeFor(latest, "verified", "cer", "all", "all");
    expect(lat.size).toBe(latest.providers.length);
    const p = latest.providers.find((x) => x.paid)!;
    expect(pricePer1000Min(p.usd_per_hour)).toBeCloseTo((p.usd_per_hour / 60) * 1000);
    expect(pricePer1000Min(null)).toBeNull();
    const sf = speedFactor(latest, p.id, "verified", "cer");
    expect(sf.source).toBe("aggregates");
    expect(sf.value).toBeGreaterThan(0);
  });

  it("only lets http(s) links out", () => {
    expect(safeUrl("https://a.test/x")).toBe("https://a.test/x");
    expect(safeUrl("javascript:alert(1)")).toBeNull();
    expect(safeUrl("not a url")).toBeNull();
  });
});
