/**
 * Writes the SYNTHETIC fixture in test/fixtures/sample/ (docs/WEB.md, "Run data"). Tests and `vite dev` only.
 * Providers are "Model A".."Model F" and clip ids start with "DEMO": nothing here is a real result.
 * Numbers are generated per clip, then aggregated with the same rules as clipbench.scoring
 * (corpus-style mean, seeded bootstrap 95% CI, paired bootstrap with a (k + 1) / (B + 1) p value, Benjamini-Hochberg
 * per tier, channel-clustered sensitivity intervals) and carry the publication gate fields of docs/V3.md
 * (languages[].gate, eligible, coverage, ranking), so every view is internally consistent.
 *
 * Run: bun run scripts/gen-sample.ts
 */
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";

// Test and dev fixture only: never shipped in a production build (see src/lib/data.ts).
const OUT = join(import.meta.dir, "..", "test", "fixtures", "sample");

type Tag =
  | "background_music"
  | "multiple_speakers"
  | "fast_speech"
  | "code_switching"
  | "slang_or_dialect"
  | "noisy"
  | "caption_not_verbatim";

const TAG_SHARE: Record<Tag, number> = {
  background_music: 0.31,
  fast_speech: 0.26,
  multiple_speakers: 0.21,
  slang_or_dialect: 0.17,
  code_switching: 0.13,
  noisy: 0.06,
  caption_not_verbatim: 0.08,
};

// Neutral ids only: real product names must never sit on fake numbers.
// Data policy fields are placeholders too (see docs/WEB.md, provider fields).
const ALL_PROVIDERS = [
  { id: "model-a", label: "Model A", model_id: "model-a-v1", usd_per_hour: 0.258, paid: true, uploads_audio: true, base: 0.062, lat: 900, retention_days: 0, trains_by_default: false, opt_out: "not_needed" },
  { id: "model-b", label: "Model B", model_id: "model-b-v1", usd_per_hour: 0.15, paid: true, uploads_audio: true, base: 0.078, lat: 3800, retention_days: 30, trains_by_default: true, opt_out: "applied" },
  { id: "model-c", label: "Model C", model_id: "model-c-v1", usd_per_hour: 0.4, paid: true, uploads_audio: true, base: 0.058, lat: 2100, retention_days: 0, trains_by_default: false, opt_out: "applied" },
  { id: "model-d", label: "Model D", model_id: "model-d-v1", usd_per_hour: 0.12, paid: true, uploads_audio: true, base: 0.068, lat: 2600, retention_days: 55, trains_by_default: true, opt_out: "not_offered" },
  { id: "model-e", label: "Model E", model_id: "model-e-v1", usd_per_hour: 0.111, paid: true, uploads_audio: true, base: 0.088, lat: 700, retention_days: 30, trains_by_default: false, opt_out: "applied" },
  { id: "model-f", label: "Model F", model_id: "model-f-v1", usd_per_hour: 0, paid: false, uploads_audio: false, base: 0.112, lat: 5200, retention_days: null, trains_by_default: null, opt_out: "not_needed" },
] as const;

type PId = (typeof ALL_PROVIDERS)[number]["id"];

/** How much each clip type multiplies a model's error rate. This is where the story lives. */
const MULT: Record<PId, Partial<Record<Tag, number>>> = {
  "model-a": { background_music: 1.9, fast_speech: 0.95, slang_or_dialect: 1.35, multiple_speakers: 1.2, code_switching: 1.55, noisy: 1.5 },
  "model-b": { background_music: 1.55, fast_speech: 1.4, slang_or_dialect: 1.35, multiple_speakers: 1.25, code_switching: 1.45, noisy: 1.6 },
  "model-c": { background_music: 1.15, fast_speech: 1.55, slang_or_dialect: 1.3, multiple_speakers: 1.1, code_switching: 1.4, noisy: 1.3 },
  "model-d": { background_music: 1.35, fast_speech: 1.3, slang_or_dialect: 0.95, multiple_speakers: 1.5, code_switching: 1.0, noisy: 1.5 },
  "model-e": { background_music: 2.1, fast_speech: 1.5, slang_or_dialect: 1.45, multiple_speakers: 1.45, code_switching: 1.2, noisy: 1.9 },
  "model-f": { background_music: 2.3, fast_speech: 1.7, slang_or_dialect: 1.55, multiple_speakers: 1.6, code_switching: 1.45, noisy: 2.1 },
};

const BUCKETS = ["<15s", "15-30s", "30-60s", "60-90s"] as const;

// Small seeded RNG (mulberry32) so the sample is stable across regenerations.
function rng(seed: number) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function normal(r: () => number) {
  const u = Math.max(r(), 1e-9);
  const v = r();
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
}

const ID_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789";
function clipId(r: () => number) {
  let s = "DEMO";
  for (let i = 0; i < 7; i++) s += ID_CHARS[Math.floor(r() * ID_CHARS.length)];
  return s;
}

const CHANNELS = ["Demo channel 1", "Demo channel 2", "Demo channel 3", "Demo channel 4", "Demo channel 5", "Demo channel 6", "Demo channel 7", "Demo channel 8"];

type Clip = { video_id: string; tier: string; duration_s: number; channel: string; tags: Tag[]; kind: "short" | "window"; start_s: number | null; category: string; audio_condition: "music" | "no_music"; punctuated: boolean; conditions: string[] };

/** docs/V3.md audio conditions (A4). Three of them come from tags; the rest are drawn from their own stream. */
const CONDITIONS = ["music", "sfx", "multiple_speakers", "fast_speech", "whisper_shout", "outdoor_noise", "code_switching"] as const;
const EXTRA_SHARE: Record<string, number> = { sfx: 0.18, whisper_shout: 0.11, outdoor_noise: 0.14 };
/** How much the conditions without a tag multiply a model's error rate. */
const COND_MULT: Record<PId, Record<string, number>> = {
  "model-a": { sfx: 1.2, whisper_shout: 1.5, outdoor_noise: 1.4 },
  "model-b": { sfx: 1.3, whisper_shout: 1.4, outdoor_noise: 1.5 },
  "model-c": { sfx: 1.1, whisper_shout: 1.3, outdoor_noise: 1.2 },
  "model-d": { sfx: 1.25, whisper_shout: 1.6, outdoor_noise: 1.35 },
  "model-e": { sfx: 1.5, whisper_shout: 1.7, outdoor_noise: 1.8 },
  "model-f": { sfx: 1.6, whisper_shout: 2.0, outdoor_noise: 1.9 },
};

const CATS = ["talking_head", "vlog_music", "gaming", "comedy_skit", "cooking_tutorial", "news_explainer", "other"];

function makeClips(r: () => number, tier: string, n: number): Clip[] {
  const clips: Clip[] = [];
  // Separate stream for short/window so adding it did not shift the numbers above.
  const rk = rng(n * 7919 + tier.length);
  const rc = rng(n * 104729 + tier.length * 13);
  for (let i = 0; i < n; i++) {
    const tags = (Object.keys(TAG_SHARE) as Tag[]).filter((t) => r() < TAG_SHARE[t]);
    const d = r();
    const duration_s = Math.round((d < 0.18 ? 8 + r() * 7 : d < 0.62 ? 15 + r() * 15 : d < 0.9 ? 30 + r() * 30 : 60 + r() * 28) * 10) / 10;
    const kind = rk() < 0.22 ? "window" : "short";
    const start_s = kind === "window" ? Math.round(30 + rk() * 600) : null;
    const category = tags.includes("background_music") && rk() < 0.6 ? "vlog_music" : CATS[Math.floor(rk() * CATS.length)]!;
    const audio_condition = tags.includes("background_music") || category === "vlog_music" ? "music" : "no_music";
    const conditions = [
      ...(audio_condition === "music" ? ["music"] : []),
      ...(["multiple_speakers", "fast_speech", "code_switching"] as const).filter((t) => tags.includes(t)),
      ...Object.keys(EXTRA_SHARE).filter((c) => rc() < EXTRA_SHARE[c]!),
    ].sort((a, b) => CONDITIONS.indexOf(a as never) - CONDITIONS.indexOf(b as never));
    clips.push({ video_id: clipId(r), tier, duration_s, channel: CHANNELS[Math.floor(r() * CHANNELS.length)]!, tags, kind, start_s, category, audio_condition, punctuated: rk() < 0.45, conditions });
  }
  return clips;
}

function bucket(s: number) {
  return s < 15 ? "<15s" : s < 30 ? "15-30s" : s < 60 ? "30-60s" : "60-90s";
}

const mean = (xs: number[]) => xs.reduce((a, b) => a + b, 0) / Math.max(xs.length, 1);
const pct = (xs: number[], q: number) => {
  const s = [...xs].sort((a, b) => a - b);
  const i = (s.length - 1) * q;
  const lo = Math.floor(i);
  const hi = Math.ceil(i);
  return s[lo]! + (s[hi]! - s[lo]!) * (i - lo);
};

function bootstrap(xs: number[], seed: number, B = 400) {
  const r = rng(seed);
  const out: number[] = [];
  for (let b = 0; b < B; b++) {
    let s = 0;
    for (let i = 0; i < xs.length; i++) s += xs[Math.floor(r() * xs.length)]!;
    out.push(s / xs.length);
  }
  return [pct(out, 0.025), pct(out, 0.975)] as const;
}

const round = (x: number, d = 4) => Math.round(x * 10 ** d) / 10 ** d;

/**
 * Channel-clustered bootstrap (docs/V3.md "Aggregates" / "Paired"): resample whole channels, as clipbench.scoring.stats
 * does for its sensitivity analysis. Returns the interval, the share of resamples on the other side of zero and n.
 */
function clusterBootstrap(values: number[], channels: string[], seed: number, B = 200) {
  const groups = new Map<string, number[]>();
  values.forEach((v, i) => (groups.get(channels[i]!) ?? groups.set(channels[i]!, []).get(channels[i]!)!).push(v));
  const keys = [...groups.keys()].sort();
  const r = rng(seed);
  const out: number[] = [];
  for (let b = 0; b < B; b++) {
    let sum = 0;
    let n = 0;
    for (let k = 0; k < keys.length; k++) {
      const g = groups.get(keys[Math.floor(r() * keys.length)]!)!;
      for (const v of g) sum += v;
      n += g.length;
    }
    out.push(sum / Math.max(n, 1));
  }
  const m = mean(values);
  const opposite = out.filter((x) => (m >= 0 ? x <= 0 : x >= 0)).length;
  return { lo: pct(out, 0.025), hi: pct(out, 0.975), p: Math.min(1, (2 * (opposite + 1)) / (B + 1)), n: keys.length };
}

/** Benjamini-Hochberg within each tier, like clipbench/report/findings.py adjust_paired (q = 0.05). */
function adjustPaired(rows: Record<string, unknown>[], q = 0.05) {
  for (const r of rows) {
    r.verdict_raw = r.verdict;
    r.p_adjusted = null;
  }
  for (const tier of new Set(rows.map((r) => r.tier as string))) {
    const g = rows.filter((r) => r.tier === tier && Number.isFinite(r.p_value as number)).sort((a, b) => (a.p_value as number) - (b.p_value as number));
    const m = g.length;
    let min = 1;
    for (let i = m - 1; i >= 0; i--) {
      min = Math.min(min, ((g[i]!.p_value as number) * m) / (i + 1));
      g[i]!.p_adjusted = round(Math.min(1, min), 4);
    }
  }
  for (const r of rows) if ((r.verdict === "a_better" || r.verdict === "b_better") && !((r.p_adjusted as number | null) != null && (r.p_adjusted as number) <= q)) r.verdict = "no_difference";
}

/** Publication gate fields (docs/V3.md "Language status"), computed the way clipbench/report/rules.py does. */
function gateFor(clipsV: Clip[], providers: readonly { id: string }[], minCoverage = 0.9) {
  const counts = new Map<string, number>();
  for (const c of clipsV) counts.set(c.channel, (counts.get(c.channel) ?? 0) + 1);
  const cats = [...new Set(clipsV.map((c) => c.category))].filter((c) => c !== "other").sort();
  const share = clipsV.length ? round(Math.max(...counts.values()) / clipsV.length) : null;
  const problems = [
    ...(counts.size < 30 ? [`${counts.size} channels, needs at least 30`] : []),
    ...(share != null && share > 0.05 ? [`largest channel holds ${(share * 100).toFixed(1)}% of verified clips, limit 5%`] : []),
    ...(cats.length < 6 ? [`${cats.length} categories, needs at least 6`] : []),
    ...(clipsV.length === 0 ? ["no verified clips"] : []),
  ];
  const eligible = providers.map((p) => p.id).sort(); // every synthetic model scores every clip: coverage 1.0
  const gate = { verified_clips_ok: clipsV.length >= 100, providers_ok: eligible.length >= 6, diversity_ok: problems.length === 0 };
  return {
    lang: "ja",
    verified_clips: clipsV.length,
    providers_ok: eligible.length,
    published: Object.values(gate).every(Boolean),
    channels: counts.size,
    categories: cats,
    largest_channel_share: share,
    providers_eligible: eligible,
    providers_excluded: [] as unknown[],
    min_coverage: minCoverage,
    verified_diversity: { channels: counts.size, largest_channel_share: share, categories: cats, passes: problems.length === 0, problems },
    gate,
  };
}

function makeRun(runId: string, createdAt: string, seed: number, drift: Record<PId, number>, include: readonly PId[], versions: Partial<Record<PId, string>>, v3 = false) {
  const r = rng(seed);
  const PROVIDERS = ALL_PROVIDERS.filter((p) => include.includes(p.id)).map((p) => ({ ...p, model_id: versions[p.id] ?? p.model_id }));
  const clipsV = makeClips(r, "verified", 150);
  const clipsC = makeClips(r, "creator", 240);
  clipsV.forEach((c, i) => (c.channel = `Demo channel ${String((i % 40) + 1).padStart(2, "0")}`));
  const allClips = [...clipsV, ...clipsC];

  type V2 = { timing_med: number | null; within: number | null; nonspeech_min: number; halluc_words: number | null; punct: number | null; casing: number | null; cs: number | null };
  type Row = { provider: PId; video_id: string; tier: string; metric: string; error_rate: number; hallucination_rate: number; latency_ms: number; clip: Clip; v2: V2 };
  // Synthetic v2 caption metrics (docs/methodology_v2_metrics.md). Model B returns no word timings, to exercise n/a.
  const r2v = rng(seed * 31 + 5);
  const TIMING: Record<PId, number | null> = { "model-a": 70, "model-b": null, "model-c": 120, "model-d": 180, "model-e": 95, "model-f": 240 };
  const HALL: Record<PId, number> = { "model-a": 0.4, "model-b": 0.3, "model-c": 0.25, "model-d": 0.6, "model-e": 2.4, "model-f": 3.6 };
  const PUNCT: Record<PId, number> = { "model-a": 0.82, "model-b": 0.74, "model-c": 0.86, "model-d": 0.7, "model-e": 0.66, "model-f": 0.58 };
  const v2For = (p: PId, clip: Clip, cer: number): V2 => {
    const base = TIMING[p];
    const timing_med = base == null ? null : Math.round(base * Math.exp(normal(r2v) * 0.3) * (clip.audio_condition === "music" ? 1.3 : 1));
    const within = timing_med == null ? null : Math.max(0, Math.min(1, 1 - timing_med / 600 + normal(r2v) * 0.05));
    const nonspeech_min = (clip.duration_s / 60) * (clip.audio_condition === "music" ? 0.35 : 0.12) * (0.6 + r2v() * 0.8);
    const halluc_words = base == null ? null : Math.max(0, Math.round(HALL[p] * nonspeech_min * (clip.audio_condition === "music" ? 1.5 : 0.6) * Math.exp(normal(r2v) * 0.5)));
    const punct = clip.punctuated ? Math.max(0, Math.min(1, PUNCT[p] + normal(r2v) * 0.08)) : null;
    const casing = clip.punctuated ? Math.max(0, Math.min(1, PUNCT[p] + 0.1 + normal(r2v) * 0.05)) : null;
    const cs = clip.tags.includes("code_switching") ? Math.min(0.95, cer * (1.4 + r2v() * 0.8)) : null;
    return { timing_med, within, nonspeech_min, halluc_words, punct, casing, cs };
  };
  const rows: Row[] = [];
  for (const clip of allClips) {
    const difficulty = Math.exp(normal(r) * 0.25);
    for (const p of PROVIDERS) {
      let m = p.base * drift[p.id] * difficulty;
      for (const t of clip.tags) m *= MULT[p.id][t] ?? 1;
      for (const c of clip.conditions) m *= COND_MULT[p.id][c] ?? 1;
      if (clip.tier === "creator") m *= 1.22; // creator captions are not verbatim, so "errors" go up for everyone
      const cer = Math.min(0.95, Math.max(0, m * Math.exp(normal(r) * 0.35)));
      const reading = Math.max(0, cer * (0.78 + r() * 0.08));
      const halluc = p.id === "model-e" || p.id === "model-f" ? (r() < 0.12 ? r() * 0.08 : 0) : r() < 0.03 ? r() * 0.03 : 0;
      const latency = Math.round(p.lat * (0.6 + clip.duration_s / 40) * Math.exp(normal(r) * 0.2));
      const v2 = v2For(p.id, clip, cer);
      rows.push({ provider: p.id, video_id: clip.video_id, tier: clip.tier, metric: "cer", error_rate: cer, hallucination_rate: halluc, latency_ms: latency, clip, v2 });
      rows.push({ provider: p.id, video_id: clip.video_id, tier: clip.tier, metric: "cer_reading", error_rate: reading, hallucination_rate: halluc, latency_ms: latency, clip, v2: { ...v2, cs: v2.cs == null ? null : v2.cs * 0.85 } });
    }
  }

  const slices: { kind: string; name: string; test: (c: Clip) => boolean }[] = [
    { kind: "all", name: "all", test: () => true },
    ...(Object.keys(TAG_SHARE) as Tag[]).map((t) => ({ kind: "tag", name: t, test: (c: Clip) => c.tags.includes(t) })),
    ...BUCKETS.map((b) => ({ kind: "duration", name: b, test: (c: Clip) => bucket(c.duration_s) === b })),
    ...CATS.map((k) => ({ kind: "category", name: k, test: (c: Clip) => c.category === k })),
    ...["music", "no_music"].map((k) => ({ kind: "audio_condition", name: k, test: (c: Clip) => c.audio_condition === k })),
    ...CONDITIONS.map((k) => ({ kind: "condition", name: k, test: (c: Clip) => c.conditions.includes(k) })),
  ];
  const v2agg = (g: Row[]) => {
    const t = g.filter((x) => x.v2.timing_med != null);
    const ns = g.filter((x) => x.v2.halluc_words != null && x.v2.nonspeech_min > 0);
    const pu = g.filter((x) => x.v2.punct != null);
    const cs = g.filter((x) => x.v2.cs != null);
    const nsMin = ns.reduce((a, x) => a + x.v2.nonspeech_min, 0);
    return {
      timing_median_ms: t.length ? Math.round(pct(t.map((x) => x.v2.timing_med!), 0.5)) : null,
      timing_p90_ms: t.length ? Math.round(pct(t.map((x) => x.v2.timing_med! * 2.1), 0.9)) : null,
      timing_within_200ms: t.length ? round(mean(t.map((x) => x.v2.within!))) : null,
      timing_n_clips: t.length,
      halluc_per_nonspeech_min: ns.length && nsMin > 0 ? round(ns.reduce((a, x) => a + x.v2.halluc_words!, 0) / nsMin, 3) : null,
      nonspeech_n_clips: ns.length,
      punct_f1: pu.length ? round(mean(pu.map((x) => x.v2.punct!))) : null,
      casing_acc: pu.length ? round(mean(pu.map((x) => x.v2.casing!))) : null,
      punct_n_clips: pu.length,
      codeswitch_error_rate: cs.length ? round(mean(cs.map((x) => x.v2.cs!))) : null,
      codeswitch_n_clips: cs.length,
    };
  };

  const LATTICE: Record<PId, number> = { "model-a": 0.9, "model-b": 0.86, "model-c": 0.92, "model-d": 0.84, "model-e": 0.89, "model-f": 0.81 };
  const aggregates: Record<string, unknown>[] = [];
  const paired: Record<string, unknown>[] = [];
  let s = 1;
  for (const tier of ["verified", "creator"]) {
    for (const metric of ["cer", "cer_reading"]) {
      for (const sl of slices) {
        const byP = new Map<PId, Row[]>();
        for (const p of PROVIDERS) byP.set(p.id, rows.filter((x) => x.tier === tier && x.metric === metric && x.provider === p.id && sl.test(x.clip)));
        for (const p of PROVIDERS) {
          const g = byP.get(p.id)!;
          if (!g.length) continue;
          const er = g.map((x) => x.error_rate);
          const [lo, hi] = bootstrap(er, s++);
          const lat3 = LATTICE[p.id];
          const audioH = g.reduce((a, x) => a + x.clip.duration_s, 0) / 3600;
          const lat = g.map((x) => x.latency_ms);
          aggregates.push({
            provider: p.id, model_id: p.model_id, tier, metric, slice_kind: sl.kind, slice: sl.name,
            n_clips: g.length, error_rate: round(mean(er)), ci_low: round(lo), ci_high: round(hi), too_small: g.length < 15,
            hallucination_rate: round(mean(g.map((x) => x.hallucination_rate))), long_output_rate: round(mean(g.map((x) => (x.hallucination_rate > 0.05 ? 1 : 0)))),
            cost_usd_total: round(audioH * p.usd_per_hour, 4), audio_hours: round(audioH, 4), cost_per_hour: p.usd_per_hour,
            latency_p50: Math.round(pct(lat, 0.5)), latency_p95: Math.round(pct(lat, 0.95)),
            rtf: round(mean(g.map((x) => x.latency_ms / 1000 / x.clip.duration_s)), 3),
            ...v2agg(g),
            // A7: scored against the closest valid path through the reference's alternatives (synthetic factor).
            error_rate_lattice: round(mean(er) * lat3), ci_low_lattice: round(lo * lat3), ci_high_lattice: round(hi * lat3),
            // docs/V3.md "Aggregates": gate and sensitivity columns.
            eligible: true, coverage: 1,
            mean_clip_error_rate: round(mean(er)),
            ...(() => {
              const c = clusterBootstrap(er, g.map((x) => x.clip.channel), s++);
              return { ci_low_cluster: round(c.lo), ci_high_cluster: round(c.hi), n_channels: c.n };
            })(),
          });
        }
        const ids = [...PROVIDERS.map((p) => p.id)].sort();
        for (let i = 0; i < ids.length; i++) {
          for (let j = i + 1; j < ids.length; j++) {
            const a = byP.get(ids[i]!)!;
            const b = byP.get(ids[j]!)!;
            if (!a.length) continue;
            const diffs = a.map((x, k) => x.error_rate - b[k]!.error_rate);
            const d = mean(diffs);
            const [lo, hi] = bootstrap(diffs, s++);
            const r2 = rng(s++);
            let opposite = 0;
            for (let k = 0; k < 400; k++) {
              let sum = 0;
              for (let q = 0; q < diffs.length; q++) sum += diffs[Math.floor(r2() * diffs.length)]!;
              const m = sum / diffs.length;
              if (Math.sign(m) !== Math.sign(d) || m === 0) opposite++;
            }
            // (k + 1) / (B + 1), like clipbench.scoring.stats: a bootstrap p value is never 0.
            const p = Math.min(1, (2 * (opposite + 1)) / 401);
            const verdict = lo > 0 ? "b_better" : hi < 0 ? "a_better" : "no_difference";
            const c = clusterBootstrap(diffs, a.map((x) => x.clip.channel), s++);
            paired.push({ tier, metric, slice_kind: sl.kind, slice: sl.name, provider_a: ids[i], provider_b: ids[j], n_clips: a.length, diff: round(d), ci_low: round(lo), ci_high: round(hi), p_value: round(p, 3), verdict, ci_low_cluster: round(c.lo), ci_high_cluster: round(c.hi), p_value_cluster: round(c.p, 3), n_channels: c.n });
          }
        }
      }
    }
  }

  adjustPaired(paired);
  const language = gateFor(clipsV, PROVIDERS);
  // docs/V3.md "Ranking": slice all, eligible providers, error_rate then id; {} while unpublished.
  const ranking: Record<string, Record<string, unknown[]>> = {};
  if (language.published)
    for (const a of aggregates as { tier: string; metric: string; slice_kind: string; provider: string; error_rate: number; ci_low: number; ci_high: number; n_clips: number }[]) {
      if (a.slice_kind !== "all") continue;
      ((ranking[a.tier] ??= {})[a.metric] ??= []).push(a);
    }
  for (const t of Object.values(ranking))
    for (const [m, rowsM] of Object.entries(t))
      t[m] = (rowsM as { provider: string; error_rate: number; ci_low: number; ci_high: number; n_clips: number }[])
        .sort((x, y) => x.error_rate - y.error_rate || x.provider.localeCompare(y.provider))
        .map((x, i) => ({ provider: x.provider, rank: i + 1, error_rate: x.error_rate, ci_low: x.ci_low, ci_high: x.ci_high, n_clips: x.n_clips }));

  const per_clip = rows
    .filter((x) => x.metric === "cer")
    .map((x) => ({ provider: x.provider, video_id: x.video_id, tier: x.tier, metric: "cer", error_rate: round(x.error_rate), hallucination_rate: round(x.hallucination_rate), latency_ms: x.latency_ms }));

  const stability = PROVIDERS.filter((p) => p.paid).flatMap((p) =>
    clipsV.slice(0, 20).map((c) => ({ provider: p.id, video_id: c.video_id, n_repeats: 3, text_disagreement: round(p.id === "model-d" ? 0.02 + r() * 0.05 : r() * 0.012), latency_mean: Math.round(p.lat * (1 + r() * 0.3)), latency_std: Math.round(p.lat * 0.1 * r()) })),
  );

  // Synthetic error patterns. Hand-written, not from any caption.
  const SUBS: Record<PId, [string, string, number][]> = {
    "model-a": [["音楽", "おんがく", 9], ["行って", "言って", 7], ["めっちゃ", "めちゃ", 5], ["ですね", "ですか", 4]],
    "model-b": [["ちょっと", "ちょと", 8], ["本当に", "ほんとに", 6], ["食べた", "食べたい", 4]],
    "model-c": [["やばい", "ヤバい", 6], ["ね", "", 5], ["じゃん", "じゃない", 3]],
    "model-d": [["えっと", "", 11], ["そう", "そうそう", 4], ["マジで", "まじで", 3]],
    "model-e": [["ありがとう", "ありがとうございます", 7], ["今日", "京", 5], ["(音楽)", "ご視聴ありがとう", 4]],
    "model-f": [["新しい", "あたらしい", 9], ["カフェ", "家へ", 6], ["行く", "いく", 5]],
  };
  const errorsProviders: Record<string, unknown> = {};
  for (const p of PROVIDERS) {
    const mine = rows.filter((x) => x.provider === p.id && x.tier === "verified" && x.metric === "cer");
    const worst = [...mine].sort((a, b) => b.error_rate - a.error_rate).slice(0, 5);
    errorsProviders[p.id] = {
      top_substitutions: SUBS[p.id],
      worst_clips: worst.map((w) => ({ video_id: w.video_id, error_rate: round(w.error_rate), others_median: round(pct(rows.filter((x) => x.video_id === w.video_id && x.metric === "cer" && x.provider !== p.id).map((x) => x.error_rate), 0.5)) })),
    };
  }
  const examples = PROVIDERS.flatMap((p) =>
    SUBS[p.id].slice(0, 2).map(([ref, hyp]) => ({ provider: p.id, video_id: clipsV[Math.floor(r() * clipsV.length)]!.video_id, ref_snippet: ref, hyp_snippet: hyp || "(dropped)" })),
  );

  return {
    run_id: runId,
    created_at: createdAt,
    language: "ja",
    git_commit: "0000000demo",
    sample_seed: 7,
    metrics: [
      { name: "cer", primary: true, label: "Character error rate" },
      { name: "cer_reading", primary: false, label: "Reading-normalised CER" },
    ],
    providers: PROVIDERS.map((p) => ({ id: p.id, model_id: p.model_id, label: p.label, pricing_checked_on: createdAt.slice(0, 10), usd_per_hour: p.usd_per_hour, paid: p.paid, uploads_audio: p.uploads_audio, retention_days: p.retention_days, trains_by_default: p.trains_by_default, opt_out: p.opt_out })),
    aggregates,
    paired,
    stability,
    findings: [
      { tier: "verified", text: "DRAFT: verify. Synthetic demo finding: Model C has the lowest CER on clips with background music; Model A leads on fast talkers.", source: "aggregates, tier=verified, slice=tag:background_music" },
      { tier: "verified", text: "DRAFT: verify. Synthetic demo finding: Model D handles slang and code-switching best but repeats differ more between runs.", source: "aggregates + stability" },
      { tier: "verified", text: "DRAFT: verify. Synthetic demo finding: the local model is free but makes about twice the errors of the best paid model.", source: "aggregates, tier=verified, slice=all" },
    ],
    errors: { providers: errorsProviders, examples },
    clips: allClips.map((c) => ({ video_id: c.video_id, tier: c.tier, duration_s: c.duration_s, channel: c.channel, tags: c.tags, kind: c.kind, start_s: c.start_s, category: c.category, audio_condition: c.audio_condition, conditions: c.conditions })),
    languages: [language],
    ranking,
    per_clip,
    ...(v3 ? v3Sections({ createdAt, providers: PROVIDERS, rows, clips: clipsV, aggregates, seed }) : NO_V3),
    limitations: [
      "This is synthetic demo data. Model names and clip ids are placeholders, not results.",
      "One language (Japanese) and short-form clips only; results do not transfer to long audio.",
      "Slices with fewer than 15 clips are marked too small to conclude.",
      "Creator captions are not verbatim, so the creator tier measures agreement, not accuracy.",
    ],
  };
}

// ---------------------------------------------------------------- v3 sections (docs/V3.md, "Export additions")

const V3_KEYS = ["streaming", "semantic", "drift", "history", "sweeps", "formatting", "lattice", "longform", "compute", "diarization", "lid", "entities", "style", "reliability", "captions", "features", "run_meta"] as const;
/** Older runs: every v3 key present and null ("no such data"), the way the exporter writes them. */
const NO_V3 = Object.fromEntries(V3_KEYS.map((k) => [k, null])) as Record<(typeof V3_KEYS)[number], null>;

type V3Row = { provider: PId; video_id: string; tier: string; metric: string; error_rate: number; latency_ms: number; clip: Clip; v2: { timing_med: number | null } };
type V3In = { createdAt: string; providers: { id: PId; model_id: string; usd_per_hour: number; uploads_audio: boolean; lat: number }[]; rows: V3Row[]; clips: Clip[]; aggregates: Record<string, unknown>[]; seed: number };

const clamp = (x: number, lo = 0, hi = 1) => Math.min(hi, Math.max(lo, x));

/** Mean with a seeded bootstrap interval, rounded, in the shape every v3 aggregate row uses. */
function stat(xs: number[], seed: number) {
  if (!xs.length) return { value: null, ci_low: null, ci_high: null };
  const [lo, hi] = bootstrap(xs, seed, 300);
  return { value: round(mean(xs)), ci_low: round(lo), ci_high: round(hi) };
}

function v3Sections({ createdAt, providers, rows, clips, aggregates, seed }: V3In) {
  const r = rng(seed * 977 + 3);
  const day = createdAt.slice(0, 10);
  const ids = providers.map((p) => p.id);
  const verified = rows.filter((x) => x.tier === "verified" && x.metric === "cer");
  const of = (p: PId) => verified.filter((x) => x.provider === p);
  const overallErr = (p: PId) => (aggregates.find((a) => a.provider === p && a.tier === "verified" && a.metric === "cer" && a.slice_kind === "all") as { error_rate: number } | undefined)?.error_rate ?? 0.1;
  let sd = 1;
  const sliceDefs: { kind: string; name: string; test: (c: Clip) => boolean }[] = [
    { kind: "all", name: "all", test: () => true },
    ...["music", "no_music"].map((k) => ({ kind: "audio_condition", name: k, test: (c: Clip) => c.audio_condition === k })),
    ...CONDITIONS.map((k) => ({ kind: "condition", name: k, test: (c: Clip) => c.conditions.includes(k) })),
  ];

  // A1 streaming: four of the six models offer a realtime API. Per clip: final error, TTFP, TTFS, stability.
  const STREAM: Partial<Record<PId, { er: number; ttfp: number; ttfs: number; stab: number }>> = {
    "model-a": { er: 1.18, ttfp: 290, ttfs: 420, stab: 0.91 },
    "model-b": { er: 1.12, ttfp: 610, ttfs: 980, stab: 0.86 },
    "model-d": { er: 1.27, ttfp: 380, ttfs: 640, stab: 0.78 },
    "model-e": { er: 1.21, ttfp: 240, ttfs: 360, stab: 0.83 },
  };
  type SC = { provider: PId; clip: Clip; er: number; ttfp: number; ttfs: number; stab: number };
  const sclips: SC[] = [];
  for (const p of ids) {
    const sp = STREAM[p];
    if (!sp) continue;
    for (const x of of(p)) sclips.push({ provider: p, clip: x.clip, er: clamp(x.error_rate * sp.er * Math.exp(normal(r) * 0.12), 0, 0.95), ttfp: sp.ttfp * Math.exp(normal(r) * 0.25), ttfs: sp.ttfs * Math.exp(normal(r) * 0.3), stab: clamp(sp.stab + normal(r) * 0.06) });
  }
  const streamRows: Record<string, unknown>[] = [];
  const streamPaired: Record<string, unknown>[] = [];
  const sIds = ids.filter((p) => STREAM[p]).sort();
  for (const sl of sliceDefs) {
    for (const p of sIds) {
      const g = sclips.filter((x) => x.provider === p && sl.test(x.clip));
      if (!g.length) continue;
      const e = stat(g.map((x) => x.er), sd++);
      const st = stat(g.map((x) => x.stab), sd++);
      streamRows.push({
        provider: p, model_id: providers.find((q) => q.id === p)!.model_id, tier: "verified", language: "ja", slice_kind: sl.kind, slice: sl.name, n_clips: g.length, metric: "cer",
        error_rate: e.value, ci_low: e.ci_low, ci_high: e.ci_high,
        ttfp_p50_ms: Math.round(pct(g.map((x) => x.ttfp), 0.5)), ttfp_p95_ms: Math.round(pct(g.map((x) => x.ttfp), 0.95)),
        ttfs_p50_ms: Math.round(pct(g.map((x) => x.ttfs), 0.5)), ttfs_p95_ms: Math.round(pct(g.map((x) => x.ttfs), 0.95)),
        stability: st.value, stability_ci_low: st.ci_low, stability_ci_high: st.ci_high,
      });
    }
    if (sl.kind !== "all" && sl.kind !== "audio_condition") continue;
    for (let i = 0; i < sIds.length; i++)
      for (let j = i + 1; j < sIds.length; j++) {
        const a = sclips.filter((x) => x.provider === sIds[i] && sl.test(x.clip));
        const bb = sclips.filter((x) => x.provider === sIds[j] && sl.test(x.clip));
        const diffs = a.map((x, k) => x.er - bb[k]!.er);
        const d = stat(diffs, sd++);
        streamPaired.push({ tier: "verified", slice_kind: sl.kind, slice: sl.name, provider_a: sIds[i], provider_b: sIds[j], n_clips: diffs.length, diff: d.value, ci_low: d.ci_low, ci_high: d.ci_high, p_value: round(d.ci_low! > 0 || d.ci_high! < 0 ? r() * 0.02 : 0.08 + r() * 0.8, 3), verdict: d.ci_low! > 0 ? "b_better" : d.ci_high! < 0 ? "a_better" : "no_difference" });
      }
  }

  // A2 semantic: judge score per clip (share of meaning kept), calibrated against human labels.
  const semRows: Record<string, unknown>[] = [];
  for (const sl of sliceDefs) {
    for (const p of ids) {
      const g = of(p).filter((x) => sl.test(x.clip));
      if (!g.length) continue;
      const st = stat(g.map((x) => clamp(1 - x.error_rate * 1.7 + normal(r) * 0.04)), sd++);
      semRows.push({ provider: p, tier: "verified", slice_kind: sl.kind, slice: sl.name, n_clips: g.length, score: st.value, ci_low: st.ci_low, ci_high: st.ci_high });
    }
  }

  // A3 history: a weekly canary rerun on a fixed clip set. Model E gets worse in early September, Model C better when v2 ships.
  const weeks: string[] = [];
  for (let t = Date.parse("2026-06-29T06:00:00Z"); t <= Date.parse(createdAt); t += 7 * 86400000) weeks.push(new Date(t).toISOString());
  const STEP: Partial<Record<PId, { at: string; factor: number; model?: string }>> = { "model-e": { at: "2026-09-07", factor: 1.32 }, "model-c": { at: "2026-10-12", factor: 0.84, model: "model-c-v2" } };
  const history: Record<string, unknown[]> = {};
  const drift: Record<string, unknown>[] = [];
  for (const p of ids) {
    const step = STEP[p];
    const end = overallErr(p);
    const startFrom = p === "model-f" ? "2026-09-14" : "2026-01-01";
    const pts = weeks.filter((w) => w >= startFrom).map((w) => {
      const after = step ? w.slice(0, 10) >= step.at : false;
      const level = step ? (after ? end : end / step.factor) : end;
      const e = clamp(level * Math.exp(normal(r) * 0.035));
      const half = e * 0.22;
      return { date: w, run_id: `canary-${w.slice(0, 10)}`, metric: "cer", kind: "canary", n_clips: 30, model_id: step?.model && after ? step.model : providers.find((q) => q.id === p)!.model_id.replace("-v2", "-v1"), error_rate: round(e), ci_low: round(e - half), ci_high: round(e + half) };
    });
    history[p] = pts;
    if (step) {
      const i = pts.findIndex((x) => x.date.slice(0, 10) >= step.at);
      const before = pts[i - 1]!;
      const now = pts[i]!;
      drift.push({ provider: p, model_id: now.model_id, date: now.date.slice(0, 10), metric: "cer", tier: "verified", language: "ja", before: before.error_rate, after: now.error_rate, ci: [before.ci_low, before.ci_high] });
    }
  }

  // A5 sweeps: clean speech mixed with music, noise and reverb beds. Each model has its own knee.
  const KNEE: Record<PId, { s0: number; a: number }> = { "model-a": { s0: 4, a: 2.4 }, "model-b": { s0: 5, a: 2.1 }, "model-c": { s0: -2, a: 1.8 }, "model-d": { s0: 3, a: 2.6 }, "model-e": { s0: 9, a: 3.2 }, "model-f": { s0: 11, a: 3.6 } };
  const sweepPts: Record<string, unknown>[] = [];
  for (const p of ids) {
    const clean = overallErr(p) * 0.72;
    const k = KNEE[p];
    const pt = (bed: string, snr: number | null, level: string | null, e: number, h: number | null) => {
      const half = e * (0.14 + r() * 0.06);
      sweepPts.push({ provider: p, tier: "verified", language: "ja", bed, snr_db: snr, level, n_clips: 40, error_rate: round(e), ci_low: round(e - half), ci_high: round(e + half), halluc_per_nonspeech_min: h == null ? null : round(h, 2) });
    };
    pt("clean", null, null, clean, null);
    for (const bed of ["music", "noise"])
      for (const snr of [20, 10, 5, 0, -5]) {
        const lift = 1 + (k.a * (bed === "noise" ? 0.85 : 1)) / (1 + Math.exp((snr - k.s0) / 2.6));
        pt(bed, snr, null, clamp(clean * lift * Math.exp(normal(r) * 0.04)), bed === "music" ? (KNEE[p].s0 / 4 + 0.3) * lift * 0.4 : null);
      }
    for (const [lvl, f] of [["0.3", 1.08], ["0.6", 1.25], ["1.2", 1.6]] as const) pt("reverb", null, lvl, clamp(clean * f * (p === "model-c" ? 0.9 : 1) * Math.exp(normal(r) * 0.04)), null);
  }

  // A6 formatting, B15 diarization, B16 LID, B17 entities, B18 style, B19 reliability, B21 captions: one row per model, all clips.
  const F: Record<PId, number[]> = {
    // punct ER, casing, itn, der, count acc, lid acc, ent plain, ent keyterms (0 = not offered)
    "model-a": [0.18, 0.93, 0.84, 0.14, 0.78, 0.97, 0.71, 0.86],
    "model-b": [0.24, 0.9, 0.71, 0, 0, 0.93, 0.64, 0],
    "model-c": [0.15, 0.95, 0.88, 0.19, 0.71, 0.98, 0.76, 0.84],
    "model-d": [0.27, 0.86, 0.62, 0.23, 0.62, 0.88, 0.69, 0],
    "model-e": [0.31, 0.84, 0.58, 0, 0, 0.91, 0.58, 0.73],
    "model-f": [0.38, 0.8, 0.49, 0, 0, 0.85, 0.52, 0],
  };
  const ci = (v: number, w: number) => ({ ci_low: round(clamp(v - w)), ci_high: round(clamp(v + w)) });
  const formatting = ids.map((p) => {
    const [per, cas, itn] = F[p];
    const byKind = Object.fromEntries((["number", "date", "time", "currency", "percent", "unit"] as const).map((k, i) => [k, { acc: round(clamp(itn! + (i - 2.5) * 0.04 + normal(r) * 0.03)), n: [214, 61, 48, 39, 22, 57][i] }]));
    const pc = ci(per!, 0.03);
    const ic = ci(itn!, 0.04);
    return { provider: p, tier: "verified", language: "ja", slice_kind: "all", slice: "all", n_clips: 68, punct_error_rate: per, punct_ci_low: pc.ci_low, punct_ci_high: pc.ci_high, casing_acc: cas, itn_acc: itn, itn_ci_low: ic.ci_low, itn_ci_high: ic.ci_high, itn_n_spans: 441, itn_by_kind: byKind };
  });
  const diar = ids.filter((p) => F[p][3]! > 0).map((p) => {
    const [, , , der, cnt] = F[p];
    return { provider: p, tier: "verified", language: "ja", slice_kind: "all", slice: "all", n_clips: 38, der, der_ci_low: round(der! - 0.03), der_ci_high: round(der! + 0.04), speaker_count_acc: cnt, count_ci_low: round(cnt! - 0.12), count_ci_high: round(clamp(cnt! + 0.1)), missed: round(der! * 0.4), false_alarm: round(der! * 0.2), confusion: round(der! * 0.4) };
  });
  const lid = ids.map((p) => {
    const acc = F[p][5]!;
    const e = overallErr(p);
    const auto = e * (1 + (1 - acc) * 3.2);
    return { provider: p, tier: "verified", language: "ja", slice_kind: "all", slice: "all", n_clips: 150, accuracy: acc, ...ci(acc, 0.025), error_rate_hint: e, error_rate_auto: round(auto), auto_ci_low: round(auto * 0.86), auto_ci_high: round(auto * 1.16), top_confusion: acc < 0.95 ? "zh" : null };
  });
  const entities = ids.flatMap((p) => {
    const [, , , , , , plain, kt] = F[p];
    const rowsE: Record<string, unknown>[] = [{ provider: p, tier: "verified", language: "ja", slice_kind: "all", slice: "all", n_clips: 96, mode: "plain", entity_acc: plain, ...ci(plain!, 0.04), n_spans: 412, keyterms_supported: kt! > 0 }];
    if (kt! > 0) rowsE.push({ provider: p, tier: "verified", language: "ja", slice_kind: "all", slice: "all", n_clips: 96, mode: "keyterms", entity_acc: kt, ...ci(kt!, 0.035), n_spans: 412, keyterms_supported: true });
    return rowsE;
  });
  const STYLE: Record<PId, number[]> = { "model-a": [0.85, 0.4, 1, 0, 0.2], "model-b": [0.1, 0.05, 0.2, 0.8, 0], "model-c": [0.62, 0.3, 0.9, 0.1, 0.5], "model-d": [0.95, 0.8, 1, 0, 0], "model-e": [0.05, 0.02, 0, 1, 0.1], "model-f": [0.7, 0.5, 1, 0, 0] };
  const style = ids.map((p) => {
    const [f, st, pk, pm, bl] = STYLE[p];
    return { provider: p, tier: "verified", language: "ja", slice_kind: "all", slice: "all", n_clips: 150, fillers_kept: f, stutters_kept: st, profanity_kept: pk, profanity_masked: pm, bleeps_marked: bl };
  });
  const reliability = ids.flatMap((p) => {
    const pr = providers.find((q) => q.id === p)!;
    const fail = pr.uploads_audio ? 0.002 + r() * 0.012 : 0;
    const lat = of(p).map((x) => x.latency_ms);
    const base = { provider: p, p50_ms: Math.round(pct(lat, 0.5)), p99_ms: Math.round(pct(lat, 0.99)) };
    return [
      { ...base, scope: "run", n_requests: of(p).length * 2, n_runs: 1, failure_rate: round(fail), timeout_rate: round(fail * 0.4), retries_per_request: round(pr.uploads_audio ? 0.02 + r() * 0.06 : 0, 3) },
      { ...base, scope: "all", n_requests: of(p).length * 2 * 3 + 30 * 17 * 2, n_runs: 3 + 17, failure_rate: round(fail * 1.3), timeout_rate: round(fail * 0.5), retries_per_request: round(pr.uploads_audio ? 0.03 + r() * 0.05 : 0, 3), p99_ms: Math.round(base.p99_ms * 1.12), since: "2026-06-29" },
    ];
  });
  const CAP: Record<PId, number[]> = { "model-a": [80, 0.86, 14.1, 0.06, 0.03, 0.9], "model-b": [260, 0.48, 15.8, 0.15, 0.09, 0.71], "model-c": [130, 0.79, 13.6, 0.05, 0.02, 0.92], "model-d": [190, 0.68, 14.9, 0.09, 0.05, 0.83], "model-e": [105, 0.81, 16.4, 0.19, 0.11, 0.69], "model-f": [250, 0.58, 15.1, 0.11, 0.06, 0.78] };
  const captions = ids.map((p) => {
    const [st, w, cps, over, line, ok] = CAP[p];
    return { provider: p, tier: "verified", language: "ja", slice_kind: "all", slice: "all", n_clips: 150, n_cues: 1840, cue_start_median_ms: st, cue_end_median_ms: Math.round(st! * 1.4), cue_within_200ms: w, cps_median: cps, cps_over: over, line_over: line, readable: ok };
  });

  // A8 long-form: whole videos, 3 to 20 minutes. Timing drift by minute.
  const LONG: Record<PId, { done: number; chunk: number; drift: number }> = { "model-a": { done: 1, chunk: 0.1, drift: 0.6 }, "model-b": { done: 0.71, chunk: 1.4, drift: 3.1 }, "model-c": { done: 1, chunk: 0.2, drift: 1.2 }, "model-d": { done: 0.92, chunk: 0.6, drift: 4.6 }, "model-e": { done: 0.96, chunk: 0.4, drift: 11.5 }, "model-f": { done: 1, chunk: 2.2, drift: 7.8 } };
  const longRows = ids.map((p) => {
    const e = overallErr(p) * 1.12;
    return { provider: p, tier: "verified", language: "ja", slice_kind: "all", slice: "all", n_videos: 24, minutes: 262, error_rate: round(e), ci_low: round(e * 0.85), ci_high: round(e * 1.17), completed: LONG[p].done, chunk_errors_per_video: LONG[p].chunk, drift_ms_per_min: LONG[p].drift };
  });
  const byMinute: Record<string, unknown>[] = [];
  for (const p of ids) {
    const base0 = TIMING_BASE[p] ?? 200;
    for (let m = 0; m < 20; m++) {
      const lift = m < 5 ? 0 : (m - 5) * LONG[p].drift * 3;
      const med = base0 + lift + normal(r) * 6;
      byMinute.push({ provider: p, minute: m, offset_median_ms: Math.round(med), offset_p90_ms: Math.round(med * 2.1), n_words: Math.round((24 - m) * 95 * (0.8 + r() * 0.4)) });
    }
  }

  // A9 compute for the local model; B20 feature matrix; run settings.
  const docs = (p: string, k: string) => `https://example.com/${p}/docs#${k}`;
  const FEAT: Record<PId, Record<string, unknown>> = {
    "model-a": { languages: ["en", "ja", "ko", "es", "hi", "de", "fr", "pt"], batch: true, streaming: true, word_timestamps: true, diarization: true, language_id: true, custom_vocabulary: true, max_file_minutes: 600, retention: "Not stored", training_opt_out: "Not needed" },
    "model-b": { languages: ["en", "ja", "es"], batch: true, streaming: true, word_timestamps: false, diarization: false, language_id: true, custom_vocabulary: false, max_file_minutes: 120, retention: "30 days", training_opt_out: "Opt-out offered" },
    "model-c": { languages: ["en", "ja", "ko", "es", "hi", "zh"], batch: true, streaming: false, word_timestamps: true, diarization: true, language_id: true, custom_vocabulary: true, max_file_minutes: 240, retention: "Not stored", training_opt_out: "Opt-out offered" },
    "model-d": { languages: ["en", "ja", "ko", "hi"], batch: true, streaming: true, word_timestamps: true, diarization: true, language_id: true, custom_vocabulary: false, max_file_minutes: 90, retention: "55 days", training_opt_out: "Not offered" },
    "model-e": { languages: ["en", "ja", "ko", "es", "hi", "it"], batch: true, streaming: true, word_timestamps: true, diarization: "beta", language_id: true, custom_vocabulary: true, max_file_minutes: 180, retention: "30 days", training_opt_out: "Opt-out offered" },
    "model-f": { languages: ["en", "ja", "ko", "es", "hi", "zh", "de"], batch: true, streaming: false, word_timestamps: true, diarization: false, language_id: true, custom_vocabulary: "partial", max_file_minutes: null, retention: "Runs locally", training_opt_out: "Not applicable" },
  };
  const features = Object.fromEntries(
    ids.map((p) => {
      const flat: Record<string, unknown> = {};
      for (const [k, v] of Object.entries(FEAT[p])) {
        flat[k] = v;
        flat[`${k}_url`] = docs(p, k);
        flat[`${k}_checked_on`] = k === "retention" ? "2026-10-02" : day;
      }
      return [p, flat];
    }),
  );
  const local = providers.filter((p) => !p.uploads_audio);
  const compute = { machine: { name: "Demo laptop", chip: "demo chip", memory_gb: 16 }, rows: local.map((p) => ({ provider: p.id, model_id: p.model_id, machine: "Demo laptop", device: "gpu", rtfx: 14.2, model_size_mb: 1550, params_m: 809, peak_memory_mb: 3900 })) };
  const run_meta = {
    kind: "batch",
    machine: { name: "Demo laptop", chip: "demo chip", memory_gb: 16 },
    options: { diarize: true, detect_language: false, keyterms: "datasets/ja/keyterms.txt" },
    providers: Object.fromEntries(providers.map((p) => [p.id, { model: p.model_id, language: "ja", punctuate: true, ...(p.id === "model-a" ? { smart_format: true } : {}), ...(p.id === "model-f" ? { beam_size: 5, compute_type: "float16" } : {}) }])),
  };

  return {
    streaming: { pace: "real-time", run_id: `stream-${day}`, created_at: createdAt, aggregates: streamRows, paired: streamPaired },
    semantic: { published: true, judge: { model: "demo-judge-v1", prompt: "semantic-v1", provider: "demo" }, calibration: { ja: { n_pairs: 64, spearman: 0.81, mae: 0.13, calibrated_on: "2026-10-10" }, thresholds: { min_pairs: 50, min_spearman: 0.7, max_mae: 0.2 } }, aggregates: semRows },
    drift: { events: drift },
    history,
    sweeps: { sweep_id: `sweep-${day}`, points: sweepPts },
    formatting: { aggregates: formatting },
    lattice: null,
    longform: { aggregates: longRows, by_minute: byMinute },
    compute,
    diarization: { aggregates: diar, unsupported: ids.filter((p) => !(F[p][3]! > 0)) },
    lid: { aggregates: lid, unsupported: [] },
    entities: { aggregates: entities, n_terms: 180 },
    style: { aggregates: style },
    reliability: reliability,
    captions: { rule: { max_chars_per_line: 42, max_lines: 2, max_cps: 17, min_duration_ms: 833, max_duration_ms: 7000 }, formats: ["srt", "vtt"], aggregates: captions },
    features: { checked_on: day, providers: features },
    run_meta,
  };
}

const TIMING_BASE: Partial<Record<PId, number>> = { "model-a": 70, "model-b": 260, "model-c": 120, "model-d": 180, "model-e": 95, "model-f": 240 };

const RUNS = [
  // Run 1 has five models; Model F joins in run 2; Model C ships v2 in run 3 (so the changelog has something to say).
  { id: "demo-2026-08-17", at: "2026-08-17T09:00:00Z", seed: 11, drift: { "model-a": 1.04, "model-b": 1.0, "model-c": 1.18, "model-d": 1.08, "model-e": 1.0, "model-f": 1.0 }, include: ["model-a", "model-b", "model-c", "model-d", "model-e"], versions: {} },
  { id: "demo-2026-09-18", at: "2026-09-18T09:00:00Z", seed: 23, drift: { "model-a": 1.02, "model-b": 0.98, "model-c": 1.14, "model-d": 1.03, "model-e": 1.01, "model-f": 1.0 }, include: ["model-a", "model-b", "model-c", "model-d", "model-e", "model-f"], versions: {} },
  { id: "demo-2026-10-19", at: "2026-10-19T09:00:00Z", seed: 7, drift: { "model-a": 1, "model-b": 1, "model-c": 1, "model-d": 1, "model-e": 1, "model-f": 1 }, include: ["model-a", "model-b", "model-c", "model-d", "model-e", "model-f"], versions: { "model-c": "model-c-v2" } },
] as const;

mkdirSync(join(OUT, "runs"), { recursive: true });
mkdirSync(join(OUT, "findings"), { recursive: true });
const index = { demo: true, latest: RUNS[RUNS.length - 1]!.id, runs: [] as unknown[], languages: [] as unknown[] };
for (const run of RUNS) {
  const data = makeRun(run.id, run.at, run.seed, run.drift, run.include, run.versions, run.id === RUNS[RUNS.length - 1]!.id);
  if (run.id === index.latest) {
    index.languages = data.languages;
    writeFileSync(join(OUT, "findings", "ja.json"), JSON.stringify(makeFindings(data), null, 2));
  }
  writeFileSync(join(OUT, "runs", `${run.id}.json`), JSON.stringify(data));
  const n_clips: Record<string, number> = {};
  for (const c of data.clips) n_clips[c.tier] = (n_clips[c.tier] ?? 0) + 1;
  index.runs.push({ run_id: run.id, created_at: run.at, language: "ja", tiers: ["verified", "creator"], n_clips, providers: data.providers.map((p) => p.id), languages: data.languages });
}
index.runs.reverse();

/**
 * Synthetic findings in the shape the exporter writes (findings/<lang>.json): only claims the paired test backs,
 * each with a structured source pointing at the table and filters it came from.
 */
function makeFindings(data: ReturnType<typeof makeRun>) {
  type A = { provider: string; tier: string; metric: string; slice_kind: string; slice: string; error_rate: number; too_small: boolean; timing_within_200ms: number | null; halluc_per_nonspeech_min: number | null };
  type P = { tier: string; metric: string; slice_kind: string; slice: string; provider_a: string; provider_b: string; diff: number; p_value: number; p_adjusted: number | null; verdict: string; ci_low_cluster: number; ci_high_cluster: number; p_value_cluster: number };
  const aggs = data.aggregates as unknown as A[];
  const pairs = data.paired as unknown as P[];
  const label = (id: string) => data.providers.find((p) => p.id === id)!.label;
  const rows = (kind: string, slice: string) => aggs.filter((a) => a.tier === "verified" && a.metric === "cer" && a.slice_kind === kind && a.slice === slice).sort((a, b) => a.error_rate - b.error_rate);
  const test = (kind: string, slice: string, a: string, b: string) => pairs.find((p) => p.tier === "verified" && p.metric === "cer" && p.slice_kind === kind && p.slice === slice && ((p.provider_a === a && p.provider_b === b) || (p.provider_a === b && p.provider_b === a)));
  const out: unknown[] = [];
  const lead = (kind: string, slice: string, what: string, slug: string) => {
    const [w, r] = rows(kind, slice);
    const t = w && r ? test(kind, slice, w.provider, r.provider) : undefined;
    // Adjusted verdict only (docs/V3.md "Paired"); a lead whose channel-clustered interval crosses zero says so.
    if (!w || !r || !t || t.verdict === "no_difference") return;
    const p = t.p_adjusted ?? t.p_value;
    const sensitive = t.ci_low_cluster <= 0 && t.ci_high_cluster >= 0;
    out.push({
      id: `f-${slug}`, slug, status: "draft",
      text: `${label(w.provider)} makes the fewest errors ${what}: ${(w.error_rate * 100).toFixed(1)}% against ${(r.error_rate * 100).toFixed(1)}% for ${label(r.provider)}, the runner-up (${p < 0.001 ? "adjusted p < 0.001" : `adjusted p = ${p.toFixed(3)}`}).${sensitive ? " Resampling whole channels instead of clips, the interval includes no difference, so this lead may come from a few channels." : ""}`,
      source: { table: "paired", filters: { tier: "verified", metric: "cer", slice_kind: kind, slice, provider_a: w.provider, provider_b: r.provider } },
      numbers: { error_rate: w.error_rate, runner_up_error_rate: r.error_rate, p_value: t.p_value, p_adjusted: p, ...(sensitive ? { ci_low_cluster: t.ci_low_cluster, ci_high_cluster: t.ci_high_cluster, p_value_cluster: t.p_value_cluster } : {}) },
      cluster_sensitive: sensitive,
    });
  };
  lead("all", "all", "overall", "lowest-error-overall");
  lead("audio_condition", "music", "on clips with music", "lowest-error-with-music");
  lead("category", "gaming", "on gaming clips", "lowest-error-gaming");
  const all = rows("all", "all");
  const timing = all.filter((a) => a.timing_within_200ms != null).sort((a, b) => b.timing_within_200ms! - a.timing_within_200ms!)[0];
  if (timing)
    out.push({
      id: "f-best-word-timing", slug: "best-word-timing", status: "draft",
      text: `${label(timing.provider)} places the most words within 200 ms of when they are said: ${(timing.timing_within_200ms! * 100).toFixed(1)}% of matched words.`,
      source: { table: "aggregates", filters: { tier: "verified", metric: "cer", slice_kind: "all", slice: "all", sort: "timing" } },
      numbers: { timing_within_200ms: timing.timing_within_200ms },
      cluster_sensitive: false,
    });
  const music = rows("audio_condition", "music").filter((a) => a.halluc_per_nonspeech_min != null).sort((a, b) => b.halluc_per_nonspeech_min! - a.halluc_per_nonspeech_min!)[0];
  if (music)
    out.push({
      id: "f-most-hallucination-music", slug: "most-hallucination-over-music", status: "draft",
      text: `${label(music.provider)} invents the most words over music and silence: ${music.halluc_per_nonspeech_min!.toFixed(2)} words per minute of non-speech audio on clips with music.`,
      source: { table: "aggregates", filters: { tier: "verified", metric: "cer", slice_kind: "audio_condition", slice: "music", sort: "halluc" } },
      numbers: { halluc_per_nonspeech_min: music.halluc_per_nonspeech_min },
      cluster_sensitive: false,
    });
  return { lang: "ja", run_id: data.run_id, created_at: data.created_at, published: data.languages[0]!.published, findings: out };
}
writeFileSync(join(OUT, "index.json"), JSON.stringify(index, null, 2));
console.log("wrote", OUT);
