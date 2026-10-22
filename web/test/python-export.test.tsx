/**
 * The site against a REAL export: web/test/fixtures/python-export/ is written by tests/fixtures/make_v3_export.py,
 * which runs the Python pipeline (runner, scorers, streaming, sweeps, long-form, canary, judge, export) on synthetic
 * clips. If a Python shape changes, these tests fail instead of a section silently disappearing from the site.
 * Regenerate: .venv/bin/python tests/fixtures/make_v3_export.py
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router";
import type { Run, RunIndex } from "@/lib/types";
import { calibrationPasses, fallsApartAt, historyFor, inSlice, latticeFor, reliabilityFor, settingsFor, speedFactor, v3 } from "@/lib/v3";
import { aggregatesFor, eligibleProviders, excludedProviders, isPublished, languageProgress, overall, pair, rankOf, rankedRun, runLanguageStatus } from "@/lib/analysis";
import { Gated, GateBars, NotRanked } from "@/components/ui/Gate";
import { Board } from "@/pages/Leaderboard";
import { MiniLeaderboard } from "@/landing/MiniLeaderboard";
import { ClusterBadge, publishedFindings } from "@/pages/Findings";
import { badgeMessage } from "../worker/src/gate";
import { execFileSync } from "node:child_process";
import { cpSync, mkdirSync, mkdtempSync, readdirSync, statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { pick } from "@/lib/picker";
import { TooltipProvider } from "@/components/ui/Tooltip";
import LeaderViewPanel from "@/components/v3/LeaderViews";
import { viewsIn, type LeaderView } from "@/components/v3/views";
import { CanaryHistory, EveryMeasure, ReliabilitySpec, SettingsSpec, ComputeSpec } from "@/components/v3/ProviderSections";
import { JudgeCalibration } from "@/components/v3/JudgeCalibration";
import { DriftPanel } from "@/components/v3/DriftPanel";
import { StreamingBoard } from "@/pages/Streaming";

const DIR = join(__dirname, "fixtures", "python-export");
const read = <T,>(p: string): T => JSON.parse(readFileSync(join(DIR, p), "utf8"));
const index = read<RunIndex>("index.json");
const run = read<Run>(`runs/${index.latest}.json`);
const raw = run as unknown as Record<string, any>;
const x = v3(run);

const html = (node: ReactNode) =>
  renderToStaticMarkup(
    <MemoryRouter>
      <TooltipProvider>{node}</TooltipProvider>
    </MemoryRouter>,
  );

describe("python export: every v3 section parses", () => {
  it("is published and has every section", () => {
    expect(isPublished(run)).toBe(true);
    for (const k of ["streaming", "semantic", "drift", "history", "sweeps", "formatting", "lattice", "longform", "compute", "diarization", "lid", "entities", "style", "reliability", "captions", "features"] as const) {
      expect(raw[k], `export has ${k}`).not.toBeNull();
      expect(x[k], `parsed ${k}`).not.toBeNull();
    }
  });

  it("streaming: final error, latency medians and stability from the rewrite rate", () => {
    const want = raw.streaming.aggregates.find((r: any) => r.slice_kind === "all" && r.provider === "synth-b");
    const got = inSlice(x.streaming!.rows, "verified").find((r) => r.provider === "synth-b")!;
    expect(got.error_rate).toBeCloseTo(want.error_rate);
    expect(got.ttfs_p50_ms).toBeCloseTo(want.ttfs_p50_ms);
    expect(got.ttfp_p95_ms).toBeCloseTo(want.ttfp_p90_ms);
    expect(got.tail).toBe("p90");
    expect(got.stability).toBeCloseTo(1 - want.partial_rewrite_rate);
    expect(got.stability_ci_low).toBeCloseTo(1 - want.rewrite_ci_high);
    expect(x.streaming!.pace).toBe("real-time");
  });

  it("semantic: published by the calibration gate, interval flipped onto the error", () => {
    const s = x.semantic!;
    expect(s.published).toBe(raw.semantic.published);
    expect(calibrationPasses(s.calibration)).toBe(raw.semantic.calibration.passes);
    expect(s.calibration!.spearman).toBeCloseTo(raw.semantic.calibration.spearman);
    expect(s.calibration!.min_pairs).toBe(raw.semantic.calibration.thresholds.min_pairs);
    const want = raw.semantic.aggregates.find((r: any) => r.slice_kind === "all" && r.provider === "synth-a");
    const got = inSlice(s.rows, "verified").find((r) => r.provider === "synth-a")!;
    expect(got.error).toBeCloseTo(want.semantic_error);
    expect(got.ci_high).toBeCloseTo(1 - want.semantic_ci_low);
    expect(s.judge.model).toBe(raw.semantic.judge_model);
  });

  it("drift and history: canary series per provider and events with their interval", () => {
    expect(historyFor(x.history, "synth-e", "wer").length).toBe(raw.history.series["synth-e"].length);
    const ev = x.drift!.find((e) => e.kind === "score")!;
    const want = raw.drift.find((e: any) => e.kind === "score");
    expect([ev.provider, ev.date, ev.ci_low, ev.ci_high]).toEqual([want.provider, want.date, want.ci[0], want.ci[1]]);
    expect(ev.direction).toBe(want.after > want.before ? "worse" : "better");
  });

  it("sweeps: the newest sweep's primary rows, clean baseline and the doubling SNR", () => {
    const sw = x.sweeps!;
    expect(sw.beds.sort()).toEqual(["music", "noise"]);
    const rows = raw.sweeps[0].rows.filter((r: any) => r.is_primary);
    expect(sw.points.length).toBe(rows.length);
    const clean = rows.find((r: any) => r.provider === "synth-a" && r.condition === "clean");
    expect(sw.points.find((p) => p.provider === "synth-a" && p.bed === "clean")!.error_rate).toBeCloseTo(clean.error_rate);
    const dbl = rows.find((r: any) => r.provider === "synth-a" && r.bed_kind === "music" && r.doubling_snr_db != null);
    expect(fallsApartAt(sw, "synth-a", "music", "verified")).toBeCloseTo(dbl.doubling_snr_db);
  });

  it("formatting, lattice, captions and style read their real column names", () => {
    const f = inSlice(x.formatting!, "verified").find((r) => r.provider === "synth-d")!;
    const fw = raw.formatting.aggregates.find((r: any) => r.slice_kind === "all" && r.provider === "synth-d");
    expect(f.punct_error_rate).toBeCloseTo(fw.punct_error_rate);
    expect(f.itn_acc).toBeCloseTo(fw.itn_form_acc);
    const lat = latticeFor(run, "verified", "wer", "all", "all").get("synth-a")!;
    const lw = raw.aggregates.find((a: any) => a.provider === "synth-a" && a.tier === "verified" && a.metric === "wer" && a.slice_kind === "all");
    expect(lat.error_rate).toBeCloseTo(lw.error_rate_lattice);
    expect(lat.ci_low).toBeCloseTo(lw.lattice_ci_low);
    const c = inSlice(x.captions!.rows, "verified").find((r) => r.provider === "synth-a")!;
    const cw = raw.captions.aggregates.find((r: any) => r.slice_kind === "all" && r.provider === "synth-a");
    expect([c.overlap, c.cue_start_median_ms, c.cps_over, c.line_over]).toEqual([cw.overlap_ratio, cw.start_error_median_ms, cw.cps_over_limit_rate, cw.line_over_limit_rate]);
    expect(x.captions!.rule!.max_chars_per_line).toBe(raw.captions.rule.max_chars.en);
    expect(x.captions!.rule!.max_cps).toBe(raw.captions.rule.cps_limit.en);
    const st = inSlice(x.style!, "verified").find((r) => r.provider === "synth-a")!;
    const sw = raw.style.aggregates.find((r: any) => r.slice_kind === "all" && r.provider === "synth-a");
    expect([st.fillers_kept, st.stutters_kept]).toEqual([sw.filler_keep_rate, sw.repetition_keep_rate]);
  });

  it("speakers, language ID and names", () => {
    const d = inSlice(x.diarization!.rows, "verified").find((r) => r.provider === "synth-a")!;
    const dw = raw.diarization.aggregates.find((r: any) => r.slice_kind === "all" && r.provider === "synth-a");
    expect([d.der, d.speaker_count_acc, d.confusion]).toEqual([dw.der, dw.speaker_count_acc, dw.confusion_rate]);
    const l = inSlice(x.lid!.rows, "verified").find((r) => r.provider === "synth-d")!;
    const lw = raw.lid.aggregates.find((r: any) => r.slice_kind === "all" && r.provider === "synth-d");
    expect([l.accuracy, l.ci_low, l.error_rate_auto, l.error_rate_hint, l.verdict]).toEqual([lw.lid_accuracy, lw.lid_ci_low, lw.error_rate_detect, lw.error_rate_hinted, lw.verdict]);
    const e = x.entities!.rows.find((r) => r.provider === "synth-a" && r.slice_kind === "all")!;
    const ew = raw.entities.keyterms_comparison.find((r: any) => r.provider === "synth-a");
    expect([e.plain.acc, e.keyterms?.acc, e.paired?.verdict]).toEqual([ew.exact_off, ew.exact_on, ew.verdict]);
  });

  it("long-form, compute, reliability, features and settings", () => {
    const lf = x.longform!;
    expect(lf.rows.length).toBe(raw.longform.aggregates.length);
    expect(lf.by_minute.length).toBe(raw.longform.minutes.length);
    const lw = raw.longform.aggregates.find((r: any) => r.provider === "synth-e");
    const lr = lf.rows.find((r) => r.provider === "synth-e")!;
    expect([lr.drift_ms_per_min, lr.dup_per_hour, lr.completed]).toEqual([lw.drift_slope_ms_per_min, lw.dup_events_per_hour, 1 - lw.failure_rate]);
    const local = raw.compute.rows.find((r: any) => r.kind === "local");
    expect(speedFactor(run, local.provider, "verified", "wer").value).toBeCloseTo(local.rtfx ?? local.speed_factor);
    const api = raw.compute.rows.find((r: any) => r.kind === "api");
    expect(speedFactor(run, api.provider, "verified", "wer").value).toBeCloseTo(api.speed_factor);
    expect(x.compute!.machine).toContain(raw.compute.machines[0].chip);
    const rel = reliabilityFor(x.reliability, "synth-b");
    const rw = raw.reliability.run.find((r: any) => r.provider === "synth-b");
    expect([rel.run?.failure_rate, rel.run?.retries_per_request, rel.run?.p99_ms]).toEqual([rw.failure_rate, rw.mean_retries, rw.latency_p99]);
    expect(rel.all?.n_runs).toBe(raw.reliability.all_runs.find((r: any) => r.provider === "synth-b").n_runs);
    expect(x.features!.providers.length).toBe(raw.features.length);
    const fp = x.features!.providers[0]!;
    const fw = raw.features[0];
    expect(fp.cells.streaming?.url).toBe(fw.features.streaming.url);
    expect(fp.price?.value).toBeCloseTo(fw.usd_per_minute * 60);
    const settings = Object.fromEntries(settingsFor(run, "synth-a"));
    expect(settings.diarize).toBe("true");
    expect(settings.settings_hash).toBe(raw.run_meta.providers["synth-a"].settings_hash);
  });

  it("the picker ranks on the real streaming and diarization sections", () => {
    const base = { audio: "any" as const, budget: null, timing: false, latency: "any" as const, local: false };
    const st = pick(run, { ...base, mode: "streaming" });
    expect(st.rows.map((r) => r.provider).sort()).toEqual(raw.streaming.providers.map((p: any) => p.id).sort());
    const di = pick(run, { ...base, diarization: true });
    for (const r of di.eligible) expect(r.der).not.toBeNull();
  });
});

describe("python export: each view renders its section, not the empty state", () => {
  const EMPTY = /Not measured in this run|No streaming results|No calibration in the latest export/;
  const views = viewsIn(run).filter((v): v is Exclude<LeaderView, "accuracy"> => v !== "accuracy");

  it("offers every leaderboard view", () => {
    expect(views.sort()).toEqual(["captions", "entities", "formatting", "lid", "longform", "speakers", "speed", "style", "sweeps"]);
  });

  it.each(["captions", "entities", "formatting", "lid", "longform", "speakers", "speed", "style", "sweeps"] as const)("leaderboard view %s", (view) => {
    const out = html(<LeaderViewPanel view={view} run={run} tier="verified" metric="wer" kind="all" slice="all" n={1} />);
    expect(out).not.toMatch(EMPTY);
    expect(out).toContain("synth-a");
    expect(out).not.toMatch(/NaN|undefined|Infinity/);
  });

  it("streaming board", () => {
    const out = html(<StreamingBoard run={run} st={x.streaming!} tier="verified" kind="all" slice="all" />);
    for (const p of raw.streaming.providers) expect(out).toContain(p.id);
    expect(out).toContain("p90");
    expect(out).not.toMatch(/NaN|undefined/);
  });

  it("model page sections, calibration and the changelog drift panel", () => {
    const p = { run, id: "synth-e", tier: "verified" as const, metric: "wer", n: 1 };
    for (const node of [<CanaryHistory {...p} />, <EveryMeasure {...p} />, <ReliabilitySpec run={run} id="synth-e" n={1} />, <SettingsSpec run={run} id="synth-e" n={1} />, <ComputeSpec run={run} id="synth-f" n={1} />, <JudgeCalibration run={run} n={1} />, <DriftPanel run={run} />]) {
      const out = html(node);
      expect(out.length).toBeGreaterThan(200);
      expect(out).not.toMatch(EMPTY);
      expect(out).not.toMatch(/NaN|undefined/);
    }
  });
});

// ---------------------------------------------------------------- publication gate (docs/V3.md, "Publication gate fields")

const LOW = "synth-g"; // below the coverage minimum, with the lowest error rate: a client-side sort would rank it first
const status = raw.languages.find((l: any) => l.lang === raw.language);
/** The markup outside every "Not ranked" group (where an excluded provider may be named with its reason). */
const ranked = (out: string) => out.replace(/<section[^>]*data-testid="not-ranked"[\s\S]*?<\/section>/g, "");

describe("python export: the publication gate", () => {
  it("is a published language under the new gate, with one provider below the coverage minimum", () => {
    expect(status.gate).toEqual({ verified_clips_ok: true, providers_ok: true, diversity_ok: true });
    expect(status.published).toBe(true);
    expect(status.verified_clips).toBeGreaterThanOrEqual(100);
    expect(status.providers_eligible.length).toBeGreaterThanOrEqual(6);
    expect(status.verified_diversity.passes).toBe(true);
    expect(status.verified_diversity.channels).toBeGreaterThanOrEqual(30);
    expect(status.verified_diversity.largest_channel_share).toBeLessThanOrEqual(0.05);
    expect(status.verified_diversity.categories.length).toBeGreaterThanOrEqual(6);
    const ex = status.providers_excluded.find((e: any) => e.provider === LOW);
    expect(ex.coverage).toBeLessThan(status.min_coverage);
    const all = raw.aggregates.filter((a: any) => a.tier === "verified" && a.metric === "wer" && a.slice_kind === "all");
    const lowest = [...all].sort((a: any, b: any) => a.error_rate - b.error_rate)[0];
    expect(lowest.provider, "the excluded provider has the lowest raw error rate").toBe(LOW);
  });

  it("ranks in exactly the export's ranking order, for every tier and metric", () => {
    for (const [tier, byMetric] of Object.entries(raw.ranking as Record<string, Record<string, any[]>>))
      for (const [metric, rows] of Object.entries(byMetric)) {
        const got = overall(run, tier as "verified", metric);
        expect(got.map((a) => a.provider), `${tier}/${metric}`).toEqual(rows.map((r) => r.provider));
        for (const r of rows) expect(rankOf(run, r.provider, tier as "verified", metric)).toBe(r.rank);
      }
    expect(Object.keys(raw.ranking.verified ?? {}).length).toBeGreaterThan(0);
  });

  it("never ranks or compares the low-coverage provider", () => {
    expect(eligibleProviders(run).has(LOW)).toBe(false);
    expect(rankOf(run, LOW, "verified", "wer")).toBeNull();
    for (const a of run.aggregates) {
      const rows = aggregatesFor(run, { tier: a.tier, metric: a.metric, kind: a.slice_kind, slice: a.slice });
      expect(rows.some((r) => r.provider === LOW), `${a.slice_kind}/${a.slice}`).toBe(false);
    }
    expect(raw.paired.some((p: any) => p.provider_a === LOW || p.provider_b === LOW)).toBe(false);
    expect(pair(run, { tier: "verified", metric: "wer", kind: "all", slice: "all" }, LOW, "synth-a")).toBeNull();
    // Picker, batch and streaming.
    const base = { audio: "any" as const, budget: null, timing: false, latency: "any" as const, local: false };
    expect(pick(run, base).rows.map((r) => r.provider)).not.toContain(LOW);
    expect(pick(run, base).rows.map((r) => r.provider)).toEqual(raw.ranking.verified.wer.map((r: any) => r.provider));
    expect(pick(run, { ...base, mode: "streaming" }).rows.map((r) => r.provider)).not.toContain(LOW);
    // Views beyond accuracy see no row of it.
    expect(JSON.stringify(v3(rankedRun(run)))).not.toContain(LOW);
  });

  it("leaderboard, landing board and leaderboard views list it only under Not ranked, with the export's reason", () => {
    const reason = status.providers_excluded.find((e: any) => e.provider === LOW).reason as string;
    const boards = [
      html(<Board run={run} tier="verified" metric="wer" kind="all" slice="all" history={[]} highlight={null} lattice={false} />),
      html(<MiniLeaderboard run={run} />),
      html(<LeaderViewPanel view="speed" run={run} tier="verified" metric="wer" kind="all" slice="all" n={1} />),
      html(<LeaderViewPanel view="formatting" run={run} tier="verified" metric="wer" kind="all" slice="all" n={1} />),
    ];
    for (const out of boards) {
      expect(ranked(out)).not.toContain(LOW);
      expect(out).toContain("Not ranked");
      expect(out).toContain(reason);
      expect(out).not.toMatch(/NaN|undefined/);
    }
    const board = boards[0]!;
    // Ranked rows in ranking order, with coverage and the channel-clustered interval.
    const order = raw.ranking.verified.wer.map((r: any) => r.provider) as string[];
    const at = order.map((p) => board.indexOf(`/providers/${p}"`));
    expect(at.every((i) => i >= 0)).toBe(true);
    expect([...at].sort((a, b) => a - b)).toEqual(at);
    expect(board).toContain("Coverage");
    expect(board).toContain("by channel");
    expect(html(<NotRanked run={run} />)).toContain(reason);
  });

  it("the badge text, OG descriptions and the command menu never rank it", () => {
    const low = badgeMessage(run as any, LOW, run.language, "wer");
    expect(low.message).toBe(`not ranked ${run.language}`);
    expect(low.message).not.toMatch(/\d/);
    const top = raw.ranking.verified.wer[0];
    expect(badgeMessage(run as any, top.provider, run.language, "wer").message).toBe(`${(top.error_rate * 100).toFixed(1)}% WER ${run.language}`);
    expect(excludedProviders(run).map((e) => e.provider)).toEqual([LOW]);

    const dist = mkdtempSync(join(process.env.TMPDIR ?? tmpdir(), "og-py-"));
    writeFileSync(join(dist, "index.html"), `<!doctype html><html><head><title>x</title></head><body></body></html>`);
    mkdirSync(join(dist, "data"), { recursive: true });
    cpSync(join(DIR, "index.json"), join(dist, "data", "index.json"));
    cpSync(join(DIR, "runs"), join(dist, "data", "runs"), { recursive: true });
    cpSync(join(DIR, "findings"), join(dist, "data", "findings"), { recursive: true });
    execFileSync("bun", ["run", join(__dirname, "..", "scripts", "og-routes.ts")], { env: { ...process.env, OG_DIST: dist, SITE_URL: "https://bench.test" } });
    const files = (d: string): string[] => readdirSync(d).flatMap((f) => (statSync(join(d, f)).isDirectory() ? files(join(d, f)) : f.endsWith(".html") ? [join(d, f)] : []));
    const pages = files(dist);
    expect(pages.length).toBeGreaterThan(10);
    for (const f of pages) {
      const page = readFileSync(f, "utf8");
      if (f.endsWith(join("providers", `${LOW}.html`))) {
        expect(page).not.toMatch(/\d+(\.\d+)?%|rank/i);
        continue;
      }
      expect(page, f).not.toContain(LOW);
    }
    expect(readFileSync(join(dist, "lang", "en.html"), "utf8")).toContain(`${status.providers_ok} eligible providers`);
  });

  it("findings never name it, and cluster-sensitive findings get a badge", () => {
    const file = read<any>("findings/en.json");
    expect(JSON.stringify(file)).not.toContain(LOW);
    expect(file.findings.every((f: any) => typeof f.cluster_sensitive === "boolean")).toBe(true);
    expect(publishedFindings(file.findings, eligibleProviders(run)).length).toBe(file.findings.length);
    const forged = { ...file.findings[0], source: { table: "paired", filters: { provider_a: LOW, provider_b: "synth-a" } } };
    expect(publishedFindings([forged], eligibleProviders(run))).toEqual([]);
    expect(html(<ClusterBadge f={{ cluster_sensitive: true }} />)).toContain("Channel sensitive");
    expect(html(<ClusterBadge f={{ cluster_sensitive: false }} />)).toBe("");
  });

  it("the gate panel shows all three conditions", () => {
    const out = html(<GateBars status={runLanguageStatus(run)} />);
    expect(out).toContain("Verified clips");
    expect(out).toContain("Eligible providers");
    expect(out).toContain("Verified set diversity");
    const failing = { ...status, published: false, gate: { ...status.gate, diversity_ok: false }, verified_diversity: { ...status.verified_diversity, passes: false, problems: ["only 12 channels; at least 30 needed"] } };
    const bad = html(<GateBars status={failing} />);
    expect(bad).toContain("only 12 channels; at least 30 needed");
    expect(bad).toContain("not met");
  });

  it("an export with the gate fields missing renders as unpublished", () => {
    const strip = (l: any) => {
      const { gate: _g, providers_eligible: _e, providers_excluded: _x, min_coverage: _m, verified_diversity: _d, ...rest } = l;
      return rest;
    };
    const old = { ...run, ranking: undefined, languages: raw.languages.map(strip), aggregates: run.aggregates.map(({ eligible: _e, coverage: _c, ...a }) => a), paired: run.paired.map(({ p_adjusted: _p, verdict_raw: _v, ...p }) => p) } as Run;
    expect(old.languages![0]!.published).toBe(true); // the old flag alone does not publish
    expect(isPublished(old)).toBe(false);
    expect(overall(old, "verified", "wer")).toEqual([]);
    expect(aggregatesFor(old, { tier: "verified", metric: "wer", kind: "audio_condition", slice: "music" })).toEqual([]);
    expect(pair(old, { tier: "verified", metric: "wer", kind: "all", slice: "all" }, "synth-a", "synth-b")).toBeNull();
    expect(eligibleProviders(old).size).toBe(0);
    expect(badgeMessage(old as any, "synth-a", old.language, "wer").message).toBe(`not published ${old.language}`);
    const idx = { ...index, languages: (index.languages ?? []).map(strip) } as RunIndex;
    expect(languageProgress(idx).every((l) => !l.published)).toBe(true);
    const out = html(<Gated run={old}>RANKING</Gated>);
    expect(out).not.toContain("RANKING");
    expect(out).toContain("Early look");
    expect(out).toContain("Full early look");
    // A published flag with a failing gate flag is not published either.
    const half = { ...run, languages: raw.languages.map((l: any) => ({ ...l, gate: { ...l.gate, diversity_ok: false } })) } as Run;
    expect(isPublished(half)).toBe(false);
    expect(overall(half, "verified", "wer")).toEqual([]);
  });
});
