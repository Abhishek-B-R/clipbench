/** Findings deep links and the provider picker, against the synthetic fixture (test-only data). */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import type { FindingsFile, Run, RunIndex } from "@/lib/types";
import { findingHref } from "@/lib/metrics";
import { parsePickParams, pick } from "@/lib/picker";

const fixture = <T>(p: string): T => JSON.parse(readFileSync(join(__dirname, "fixtures", "sample", p), "utf8"));
const index = fixture<RunIndex>("index.json");
const run = fixture<Run>(`runs/${index.latest}.json`);
const findings = fixture<FindingsFile>("findings/ja.json");

describe("findings", () => {
  it("every finding links to the table and filters it came from", () => {
    expect(findings.findings.length).toBeGreaterThan(0);
    for (const f of findings.findings) {
      const href = findingHref(f, "ja");
      const url = new URL(href, "https://x.test");
      expect(["/compare", "/leaderboard"]).toContain(url.pathname);
      const fl = f.source.filters;
      if (fl.metric) expect(url.searchParams.get("metric")).toBe(fl.metric);
      if (fl.slice_kind && fl.slice_kind !== "all") {
        expect(url.searchParams.get("slice_kind")).toBe(fl.slice_kind);
        expect(url.searchParams.get("slice")).toBe(fl.slice);
      }
      if (f.source.table === "paired") expect(url.searchParams.get("m")).toBe(`${fl.provider_a},${fl.provider_b}`);
    }
  });
});

describe("pick a provider", () => {
  const base = { audio: "any" as const, budget: null, timing: false, latency: "any" as const, local: false };

  it("ranks by error on the matching audio slice", () => {
    const r = pick(run, { ...base, audio: "music" });
    expect(r.kind).toBe("audio_condition");
    expect(r.slice).toBe("music");
    const errs = r.eligible.map((x) => x.agg.error_rate);
    expect([...errs].sort((a, b) => a - b)).toEqual(errs);
  });

  it("rules out models without word timings when timing is needed (n/a, not zero)", () => {
    const r = pick(run, { ...base, timing: true });
    const noTiming = r.rows.filter((x) => x.timing == null);
    expect(noTiming.length).toBeGreaterThan(0);
    for (const x of noTiming) expect(x.excluded).toContain("No word timings");
  });

  it("keeps audio local when asked", () => {
    const r = pick(run, { ...base, local: true });
    expect(r.eligible.every((x) => !x.uploads)).toBe(true);
  });

  it("flags statistical ties against the top pick from the paired table", () => {
    const r = pick(run, base);
    for (const t of r.tied) expect(t.vsPick?.tied).toBe(true);
  });
});

describe("picker URL inputs", () => {
  const parse = (q: string) => parsePickParams(new URLSearchParams(q));
  it("keeps valid values", () => {
    expect(parse("audio=music&lat=1500&budget=0.5")).toEqual({ audio: "music", latency: "1500", budget: 0.5 });
    expect(parse("budget=0")).toEqual({ audio: "any", latency: "any", budget: 0 });
  });
  it("falls back to defaults for hand-edited links, never NaN or an unknown option", () => {
    for (const q of ["audio=bad&lat=bad&budget=abc", "budget=-5", "budget=Infinity", "budget=", "budget=%20"]) {
      expect(parse(q)).toEqual({ audio: "any", latency: "any", budget: null });
    }
  });
});
