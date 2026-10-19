/**
 * Writes the SYNTHETIC fixture in test/fixtures/sample/ (docs/WEB.md, "Run data"). Tests and `vite dev` only.
 * Providers are "Model A".."Model F" and clip ids start with "DEMO": nothing here is a real result.
 * Numbers are generated per clip, then aggregated with the same rules as clipbench.scoring
 * (corpus-style mean, seeded bootstrap 95% CI, paired bootstrap), so every view is internally consistent.
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

type Clip = { video_id: string; tier: string; duration_s: number; channel: string; tags: Tag[]; kind: "short" | "window"; start_s: number | null; category: string; audio_condition: "music" | "no_music"; punctuated: boolean };

const CATS = ["talking_head", "vlog_music", "gaming", "comedy_skit", "cooking_tutorial", "news_explainer", "other"];

function makeClips(r: () => number, tier: string, n: number): Clip[] {
  const clips: Clip[] = [];
  // Separate stream for short/window so adding it did not shift the numbers above.
  const rk = rng(n * 7919 + tier.length);
  for (let i = 0; i < n; i++) {
    const tags = (Object.keys(TAG_SHARE) as Tag[]).filter((t) => r() < TAG_SHARE[t]);
    const d = r();
    const duration_s = Math.round((d < 0.18 ? 8 + r() * 7 : d < 0.62 ? 15 + r() * 15 : d < 0.9 ? 30 + r() * 30 : 60 + r() * 28) * 10) / 10;
    const kind = rk() < 0.22 ? "window" : "short";
    const start_s = kind === "window" ? Math.round(30 + rk() * 600) : null;
    const category = tags.includes("background_music") && rk() < 0.6 ? "vlog_music" : CATS[Math.floor(rk() * CATS.length)]!;
    const audio_condition = tags.includes("background_music") || category === "vlog_music" ? "music" : "no_music";
    clips.push({ video_id: clipId(r), tier, duration_s, channel: CHANNELS[Math.floor(r() * CHANNELS.length)]!, tags, kind, start_s, category, audio_condition, punctuated: rk() < 0.45 });
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

function makeRun(runId: string, createdAt: string, seed: number, drift: Record<PId, number>, include: readonly PId[], versions: Partial<Record<PId, string>>) {
  const r = rng(seed);
  const PROVIDERS = ALL_PROVIDERS.filter((p) => include.includes(p.id)).map((p) => ({ ...p, model_id: versions[p.id] ?? p.model_id }));
  const clipsV = makeClips(r, "verified", 150);
  const clipsC = makeClips(r, "creator", 240);
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
            const p = Math.min(1, (2 * opposite) / 400);
            const verdict = lo > 0 ? "b_better" : hi < 0 ? "a_better" : "no_difference";
            paired.push({ tier, metric, slice_kind: sl.kind, slice: sl.name, provider_a: ids[i], provider_b: ids[j], n_clips: a.length, diff: round(d), ci_low: round(lo), ci_high: round(hi), p_value: round(p, 3), verdict });
          }
        }
      }
    }
  }

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
    clips: allClips.map((c) => ({ video_id: c.video_id, tier: c.tier, duration_s: c.duration_s, channel: c.channel, tags: c.tags, kind: c.kind, start_s: c.start_s, category: c.category, audio_condition: c.audio_condition })),
    languages: [{ lang: "ja", verified_clips: clipsV.length, providers_ok: PROVIDERS.length, published: clipsV.length >= 100 && PROVIDERS.length >= 6, channels: new Set(clipsV.map((c) => c.channel)).size, categories: [...new Set(clipsV.map((c) => c.category))].filter((c) => c !== "other") }],
    per_clip,
    limitations: [
      "This is synthetic demo data. Model names and clip ids are placeholders, not results.",
      "One language (Japanese) and short-form clips only; results do not transfer to long audio.",
      "Slices with fewer than 15 clips are marked too small to conclude.",
      "Creator captions are not verbatim, so the creator tier measures agreement, not accuracy.",
    ],
  };
}

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
  const data = makeRun(run.id, run.at, run.seed, run.drift, run.include, run.versions);
  if (run.id === index.latest) {
    index.languages = data.languages;
    writeFileSync(join(OUT, "findings", "ja.json"), JSON.stringify(makeFindings(data), null, 2));
  }
  writeFileSync(join(OUT, "runs", `${run.id}.json`), JSON.stringify(data));
  const n_clips: Record<string, number> = {};
  for (const c of data.clips) n_clips[c.tier] = (n_clips[c.tier] ?? 0) + 1;
  index.runs.push({ run_id: run.id, created_at: run.at, language: "ja", tiers: ["verified", "creator"], n_clips, providers: data.providers.map((p) => p.id) });
}
index.runs.reverse();

/**
 * Synthetic findings in the shape the exporter writes (findings/<lang>.json): only claims the paired test backs,
 * each with a structured source pointing at the table and filters it came from.
 */
function makeFindings(data: ReturnType<typeof makeRun>) {
  type A = { provider: string; tier: string; metric: string; slice_kind: string; slice: string; error_rate: number; too_small: boolean; timing_within_200ms: number | null; halluc_per_nonspeech_min: number | null };
  type P = { tier: string; metric: string; slice_kind: string; slice: string; provider_a: string; provider_b: string; diff: number; p_value: number; verdict: string };
  const aggs = data.aggregates as unknown as A[];
  const pairs = data.paired as unknown as P[];
  const label = (id: string) => data.providers.find((p) => p.id === id)!.label;
  const rows = (kind: string, slice: string) => aggs.filter((a) => a.tier === "verified" && a.metric === "cer" && a.slice_kind === kind && a.slice === slice).sort((a, b) => a.error_rate - b.error_rate);
  const test = (kind: string, slice: string, a: string, b: string) => pairs.find((p) => p.tier === "verified" && p.metric === "cer" && p.slice_kind === kind && p.slice === slice && ((p.provider_a === a && p.provider_b === b) || (p.provider_a === b && p.provider_b === a)));
  const out: unknown[] = [];
  const lead = (kind: string, slice: string, what: string, slug: string) => {
    const [w, r] = rows(kind, slice);
    const t = w && r ? test(kind, slice, w.provider, r.provider) : undefined;
    if (!w || !r || !t || t.verdict === "no_difference") return;
    out.push({
      id: `f-${slug}`, slug, status: "draft",
      text: `${label(w.provider)} makes the fewest errors ${what}: ${(w.error_rate * 100).toFixed(1)}% against ${(r.error_rate * 100).toFixed(1)}% for ${label(r.provider)}, the runner-up (${t.p_value < 0.001 ? "p < 0.001" : `p = ${t.p_value.toFixed(3)}`}).`,
      source: { table: "paired", filters: { tier: "verified", metric: "cer", slice_kind: kind, slice, provider_a: w.provider, provider_b: r.provider } },
      numbers: { error_rate: w.error_rate, runner_up_error_rate: r.error_rate, p_value: t.p_value },
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
    });
  const music = rows("audio_condition", "music").filter((a) => a.halluc_per_nonspeech_min != null).sort((a, b) => b.halluc_per_nonspeech_min! - a.halluc_per_nonspeech_min!)[0];
  if (music)
    out.push({
      id: "f-most-hallucination-music", slug: "most-hallucination-over-music", status: "draft",
      text: `${label(music.provider)} invents the most words over music and silence: ${music.halluc_per_nonspeech_min!.toFixed(2)} words per minute of non-speech audio on clips with music.`,
      source: { table: "aggregates", filters: { tier: "verified", metric: "cer", slice_kind: "audio_condition", slice: "music", sort: "halluc" } },
      numbers: { halluc_per_nonspeech_min: music.halluc_per_nonspeech_min },
    });
  return { lang: "ja", run_id: data.run_id, created_at: data.created_at, published: data.languages[0]!.published, findings: out };
}
writeFileSync(join(OUT, "index.json"), JSON.stringify(index, null, 2));
console.log("wrote", OUT);
